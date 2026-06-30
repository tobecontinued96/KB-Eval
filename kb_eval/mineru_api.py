"""MinerU official API adapter for PDF to Markdown conversion."""

from __future__ import annotations

import http.client
import json
import re
import shutil
import time
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import ssl
from urllib import error, parse, request

from kb_eval.errors import EvalError


API_BASE_URL = "https://mineru.net"

# 下载结果 zip 时偶发 SSL: UNEXPECTED_EOF_WHILE_READING —— 多半是 CDN 长连接被中间设备截断。
# 用显式 SSLContext + 短重试（指数退避）兜底，HTTPError 不在重试范围（那是 4xx/5xx）。
_DOWNLOAD_MAX_ATTEMPTS = 3
_DOWNLOAD_RETRY_BACKOFF_SECONDS = 2.0
_DOWNLOAD_HEADERS = {
    "Accept": "*/*",
    "Accept-Encoding": "identity",
    "Connection": "close",
    "User-Agent": "Dify-KB-Eval/0.1",
}


def _build_ssl_context() -> ssl.SSLContext:
    """构造一个禁用压缩、显式校验的 SSLContext。

    Python 3.10+ 的 stdlib `urlopen` 在 Windows 上默认 `context=None`，对部分 CDN
    会出现 UNEXPECTED_EOF_WHILE_READING。显式传 `ssl.create_default_context()`
    并禁用压缩可以稳定大多数情况。
    """
    ctx = ssl.create_default_context()
    # 避免使用 0-RTT 之类容易触发截断的协商特性
    try:
        ctx.options |= ssl.OP_NO_COMPRESSION
    except AttributeError:  # 极少数极旧环境下没有这个常量
        pass
    ignore_unexpected_eof = getattr(ssl, "OP_IGNORE_UNEXPECTED_EOF", None)
    if ignore_unexpected_eof is not None:
        ctx.options |= ignore_unexpected_eof
    return ctx


@dataclass(frozen=True)
class MinerUApiResult:
    markdown_path: Path
    batch_id: str
    file_id: str
    extract_progress: str
    state: str


def convert_pdf_with_mineru_api(
    pdf_path: Path,
    output_dir: Path,
    *,
    token: str,
    model_version: str = "vlm",
    timeout_seconds: int = 900,
    poll_interval_seconds: int = 5,
) -> MinerUApiResult:
    if not token.strip():
        raise EvalError("MinerU API token is required")
    output_dir.mkdir(parents=True, exist_ok=True)

    upload_info = request_upload_url(pdf_path, token=token, model_version=model_version)
    upload_file(pdf_path, upload_info["upload_url"])
    result = poll_batch_result(
        upload_info["batch_id"],
        token=token,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    file_result = find_file_result(result, pdf_path.name)
    zip_url = str(file_result.get("full_zip_url") or "")
    if not zip_url:
        raise EvalError(f"MinerU API finished without full_zip_url for {pdf_path.name}")

    zip_path = output_dir / f"{pdf_path.stem}.mineru.zip"
    download_file(zip_url, zip_path)
    markdown_path = extract_full_markdown(zip_path, output_dir, pdf_path.stem)
    return MinerUApiResult(
        markdown_path=markdown_path,
        batch_id=str(upload_info["batch_id"]),
        file_id=str(file_result.get("file_id") or upload_info.get("file_id") or ""),
        extract_progress=str(file_result.get("extract_progress") or ""),
        state=str(file_result.get("state") or ""),
    )


def request_upload_url(pdf_path: Path, *, token: str, model_version: str) -> dict[str, Any]:
    payload = {
        "enable_formula": True,
        "enable_table": True,
        "language": "ch",
        "model_version": model_version,
        "files": [{"name": pdf_path.name, "is_ocr": True, "data_id": safe_data_id(pdf_path.stem)}],
    }
    response = api_request_json(
        "POST",
        f"{API_BASE_URL}/api/v4/file-urls/batch",
        token=token,
        body=payload,
        timeout=60,
    )
    data = response.get("data")
    if not isinstance(data, dict):
        raise EvalError("MinerU upload URL response missing data")
    file_urls = data.get("file_urls")
    if not isinstance(file_urls, list) or not file_urls:
        raise EvalError("MinerU upload URL response missing file_urls")
    item = file_urls[0]
    if isinstance(item, str):
        upload_url = item
        file_id = ""
    elif isinstance(item, dict):
        upload_url = str(item.get("upload_url") or item.get("file_url") or item.get("url") or "")
        file_id = str(item.get("file_id") or "")
    else:
        upload_url = ""
        file_id = ""
    if not upload_url:
        raise EvalError("MinerU upload URL response missing upload_url")
    batch_id = str(data.get("batch_id") or "")
    if not batch_id:
        raise EvalError("MinerU upload URL response missing batch_id")
    return {
        "batch_id": batch_id,
        "file_id": file_id,
        "upload_url": upload_url,
    }


def safe_data_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("_.-")
    return (cleaned or "document")[:128]


def upload_file(pdf_path: Path, upload_url: str) -> None:
    data = pdf_path.read_bytes()
    parsed = parse.urlsplit(upload_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise EvalError("MinerU upload URL is invalid")
    connection_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_class(parsed.hostname, parsed.port, timeout=300)
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    try:
        connection.request("PUT", target, body=data, headers={"Content-Length": str(len(data))})
        response = connection.getresponse()
        detail = response.read().decode("utf-8", errors="replace")
        if response.status >= 300:
            raise EvalError(f"MinerU upload failed with HTTP {response.status}: {detail[:300]}")
    except (OSError, http.client.HTTPException) as exc:
        raise EvalError(f"MinerU upload failed: {exc}") from exc
    finally:
        connection.close()


def poll_batch_result(
    batch_id: str,
    *,
    token: str,
    timeout_seconds: int,
    poll_interval_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_progress = ""
    while time.monotonic() < deadline:
        response = api_request_json(
            "GET",
            f"{API_BASE_URL}/api/v4/extract-results/batch/{batch_id}",
            token=token,
            timeout=60,
        )
        data = response.get("data")
        if not isinstance(data, dict):
            raise EvalError("MinerU result response missing data")
        extract_result = data.get("extract_result")
        if isinstance(extract_result, list) and extract_result:
            states = [str(item.get("state") or "") for item in extract_result if isinstance(item, dict)]
            progresses = [str(item.get("extract_progress") or "") for item in extract_result if isinstance(item, dict)]
            last_progress = ", ".join(item for item in progresses if item)
            if all(state == "done" for state in states):
                return data
            if any(state == "failed" for state in states):
                raise EvalError(f"MinerU API extraction failed: {extract_result}")
        time.sleep(poll_interval_seconds)
    raise EvalError(f"MinerU API extraction timed out after {timeout_seconds}s. Last progress: {last_progress}")


def find_file_result(data: dict[str, Any], filename: str) -> dict[str, Any]:
    extract_result = data.get("extract_result")
    if not isinstance(extract_result, list):
        raise EvalError("MinerU result response missing extract_result")
    for item in extract_result:
        if not isinstance(item, dict):
            continue
        if item.get("file_name") == filename or item.get("data_id") == Path(filename).stem:
            return item
    first = next((item for item in extract_result if isinstance(item, dict)), None)
    if first:
        return first
    raise EvalError(f"MinerU result response has no result for {filename}")


def download_file(url: str, target: Path) -> None:
    """下载 MinerU 返回的结果 zip。

    CDN 偶发 `SSL: UNEXPECTED_EOF_WHILE_READING` —— 多为中间设备截断长连接。
    显式传入 `SSLContext` + 有限重试（指数退避）后，体感稳定很多。
    """
    ssl_ctx = _build_ssl_context()
    last_exc: Exception | None = None
    for attempt in range(1, _DOWNLOAD_MAX_ATTEMPTS + 1):
        req = request.Request(url, method="GET", headers=_DOWNLOAD_HEADERS)
        try:
            with request.urlopen(req, timeout=300, context=ssl_ctx) as resp:
                target.write_bytes(resp.read())
            return
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise EvalError(
                f"MinerU result download failed with HTTP {exc.code}: {detail[:300]}"
            ) from exc
        except (error.URLError, ssl.SSLError, ConnectionError, TimeoutError) as exc:
            last_exc = exc
            if attempt >= _DOWNLOAD_MAX_ATTEMPTS:
                break
            time.sleep(_DOWNLOAD_RETRY_BACKOFF_SECONDS * attempt)
    reason = getattr(last_exc, "reason", None) or str(last_exc)
    curl_error = download_file_with_curl(url, target)
    if curl_error is None:
        return
    raise EvalError(f"MinerU result download failed: {reason}; curl fallback failed: {curl_error}") from last_exc


def download_file_with_curl(url: str, target: Path) -> str | None:
    curl_path = shutil.which("curl") or shutil.which("curl.exe")
    if not curl_path:
        return "curl executable not found"

    part_path = target.with_name(f"{target.name}.part")
    command = [
        curl_path,
        "--location",
        "--fail",
        "--silent",
        "--show-error",
        "--http1.1",
        "--retry",
        str(_DOWNLOAD_MAX_ATTEMPTS),
        "--retry-all-errors",
        "--connect-timeout",
        "30",
        "--max-time",
        "300",
        "--header",
        "Accept: */*",
        "--header",
        "Accept-Encoding: identity",
        "--header",
        "Connection: close",
        "--output",
        str(part_path),
        url,
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=360)
    except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
        return str(exc)
    if result.returncode != 0:
        part_path.unlink(missing_ok=True)
        detail = (result.stderr or result.stdout or f"curl exited with {result.returncode}").strip()
        return detail[:300]
    part_path.replace(target)
    return None


def extract_full_markdown(zip_path: Path, output_dir: Path, pdf_stem: str) -> Path:
    target_path = output_dir / f"{pdf_stem}.md"
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        markdown_name = next((name for name in names if name.endswith("/full.md") or name == "full.md"), "")
        if not markdown_name:
            markdown_name = next((name for name in names if name.lower().endswith(".md")), "")
        if not markdown_name:
            raise EvalError("MinerU result zip does not contain Markdown")
        content = archive.read(markdown_name)
    target_path.write_bytes(content)
    return target_path


def api_request_json(
    method: str,
    url: str,
    *,
    token: str,
    body: dict[str, Any] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = request.Request(url, data=data, method=method, headers=headers)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise EvalError(f"MinerU API HTTP {exc.code}: {detail[:300]}") from exc
    except error.URLError as exc:
        raise EvalError(f"MinerU API request failed: {exc.reason}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EvalError(f"MinerU API returned invalid JSON: {raw[:200]}") from exc
    if not isinstance(payload, dict):
        raise EvalError("MinerU API returned non-object JSON")
    if payload.get("code") not in (0, 200, None):
        raise EvalError(f"MinerU API error: {payload.get('msg') or payload.get('message') or payload}")
    return payload
