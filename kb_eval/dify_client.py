"""Dify Knowledge Base API client used by the evaluation runner.

The evaluator expects normalized retrieval chunks with ``content``,
``document_id``, ``document_name`` and ``score``. Dify's Knowledge Base
API returns ``records[].segment`` instead, so this client is intentionally
an adapter rather than a broad SDK.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable
from urllib import error, parse, request

from kb_eval.errors import EvalError
from kb_eval.retrieval_utils import (
    dataset_name_matches,
    knowledge_base_name,
    score_dataset,
    unwrap_data,
    unwrap_meta,
)


def dify_api_base(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/v1") else f"{base}/v1"


def dify_api_url(base_url: str, path: str, query: dict[str, Any] | None = None) -> str:
    suffix = path if path.startswith("/") else f"/{path}"
    url = f"{dify_api_base(base_url)}{suffix}"
    if query:
        clean_query = {key: str(value) for key, value in query.items() if value is not None}
        url = f"{url}?{parse.urlencode(clean_query)}"
    return url


def request_dify_json(
    method: str,
    url: str,
    *,
    token: str = "",
    body: dict[str, Any] | None = None,
    timeout: float = 60,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = request.Request(url, data=data, method=method.upper(), headers=headers)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise EvalError(f"HTTP {exc.code} for Dify at {url}: {detail}") from exc
    except error.URLError as exc:
        raise EvalError(f"Cannot reach Dify at {url}: {exc.reason}") from exc
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EvalError(f"Invalid JSON response from Dify at {url}: {raw[:200]}") from exc
    if isinstance(payload, dict) and payload.get("code") not in (None, 200, "success"):
        raise EvalError(f"Dify error for {url}: {payload.get('message') or payload}")
    if not isinstance(payload, dict):
        raise EvalError(f"Unexpected JSON response from Dify at {url}: {type(payload).__name__}")
    return payload


def normalize_dify_knowledge_base(item: dict[str, Any]) -> dict[str, Any]:
    dataset_id = str(item.get("dataset_id") or item.get("id") or "").strip()
    documents = item.get("documents") or []
    document_count = item.get("document_count")
    if document_count is None:
        document_count = len(documents) if isinstance(documents, list) else 0
    retrieval_model = item.get("retrieval_model_dict")
    retrieval_model_dict = retrieval_model if isinstance(retrieval_model, dict) else {}
    return {
        "dataset_id": dataset_id,
        "id": dataset_id,
        "name": str(item.get("name") or ""),
        "display_name": str(item.get("display_name") or item.get("name") or ""),
        "vendor": str(item.get("vendor") or ""),
        "model": str(item.get("model") or ""),
        "description": str(item.get("description") or ""),
        "document_count": int(document_count or 0),
        "embedding_model": str(item.get("embedding_model") or ""),
        "embedding_model_provider": str(item.get("embedding_model_provider") or ""),
        "retrieval_model_dict": retrieval_model_dict,
    }


def build_dify_retrieval_model(retrieval_model_dict: dict[str, Any] | None, top_k: int) -> dict[str, Any]:
    model = dict(retrieval_model_dict or {})
    if not model:
        model = {
            "search_method": "semantic_search",
            "reranking_enable": False,
            "top_k": top_k,
            "score_threshold_enabled": False,
        }
    model["top_k"] = top_k
    return model


def normalize_dify_record(record: dict[str, Any]) -> dict[str, Any]:
    segment = record.get("segment")
    if not isinstance(segment, dict):
        segment = record
    document = segment.get("document")
    if not isinstance(document, dict):
        document = {}
    document_id = (
        segment.get("document_id")
        or document.get("id")
        or record.get("document_id")
        or ""
    )
    document_name = (
        document.get("name")
        or segment.get("document_name")
        or segment.get("file_name")
        or record.get("document_name")
        or ""
    )
    content = segment.get("content") or record.get("content") or ""
    score = record.get("score", segment.get("score", 0))
    return {
        "document_id": str(document_id or ""),
        "document_name": str(document_name or ""),
        "content": str(content or ""),
        "score": score or 0,
        "keywords": segment.get("keywords") or [],
        "source": {"document_name": str(document_name or "")},
    }


def records_from_dify_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    records = payload.get("records")
    if records is None:
        data = unwrap_data(payload)
        if isinstance(data, dict):
            records = data.get("records") or data.get("data")
        elif isinstance(data, list):
            records = data
    if not isinstance(records, list):
        raise EvalError("Dify retrieve response records is not a list")
    return [normalize_dify_record(item) for item in records if isinstance(item, dict)]


class DifyClient:
    def __init__(self, base_url: str, token: str = "", timeout: float = 60) -> None:
        if not token.strip():
            raise EvalError("Dify API key is required for direct connection")
        self.base_url = base_url
        self.token = token
        self.timeout = timeout
        self._retrieval_models: dict[str, dict[str, Any]] = {}

    def list_knowledge_bases(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        limit = 100
        while True:
            payload = request_dify_json(
                "GET",
                dify_api_url(self.base_url, "/datasets", {"page": page, "limit": limit}),
                token=self.token,
                timeout=self.timeout,
            )
            page_items = unwrap_data(payload)
            if not isinstance(page_items, list):
                raise EvalError("Dify knowledge base list response data is not a list")
            normalized = [
                normalize_dify_knowledge_base(item)
                for item in page_items
                if isinstance(item, dict)
            ]
            for item in normalized:
                dataset_id = str(item.get("dataset_id") or "")
                retrieval = item.get("retrieval_model_dict")
                if dataset_id and isinstance(retrieval, dict):
                    self._retrieval_models[dataset_id] = retrieval
            items.extend(normalized)
            meta = unwrap_meta(payload)
            total = int(payload.get("total") or meta.get("total") or len(items))
            has_more = bool(payload.get("has_more") or meta.get("has_more"))
            if not has_more and (len(items) >= total or len(page_items) < limit):
                break
            if not page_items:
                break
            page += 1
        return items

    def resolve_dataset_id(
        self,
        *,
        vendor: str,
        model: str,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        candidates = self.list_knowledge_bases()
        scored = [(score_dataset(item, vendor, model), item) for item in candidates]
        scored = [(score, item) for score, item in scored if score > 0]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        if not scored:
            raise EvalError(
                f"No Dify knowledge base matched vendor={vendor!r}, model={model!r}. "
                "Pass dataset_id if the dataset name/metadata is not normalized yet.",
            )
        _, best = scored[0]
        expected_name = knowledge_base_name(vendor, model)
        if expected_name and not dataset_name_matches(best, expected_name):
            actual_name = best.get("name") or best.get("display_name") or ""
            raise EvalError(
                f"Matched Dify knowledge base name {actual_name!r} does not match expected {expected_name!r}. "
                "Rename the knowledge base or pass dataset_id explicitly after confirming the target.",
            )
        dataset_id = str(best.get("dataset_id") or best.get("id") or "")
        if not dataset_id:
            raise EvalError(f"Matched Dify knowledge base has no dataset_id: {best}")
        return dataset_id, best, [item for _, item in scored[:5]]

    def validate_dataset_name(
        self,
        *,
        dataset_id: str,
        vendor: str,
        model: str,
        on_warning: Callable[[str], None] | None = None,
    ) -> dict[str, Any] | None:
        expected_name = knowledge_base_name(vendor, model)
        if not expected_name:
            return None
        for item in self.list_knowledge_bases():
            item_id = str(item.get("dataset_id") or item.get("id") or "")
            if item_id != dataset_id:
                continue
            if not dataset_name_matches(item, expected_name):
                actual_name = item.get("name") or item.get("display_name") or ""
                message = (
                    f"Dify KB name mismatch (using user-selected dataset_id): "
                    f"actual={actual_name!r} expected={expected_name!r}"
                )
                if on_warning is not None:
                    on_warning(f"[WARN] {message}")
                    return item
                raise EvalError(
                    f"Dify knowledge base name {actual_name!r} for dataset_id={dataset_id!r} "
                    f"does not match expected {expected_name!r}.",
                )
            return item
        return None

    def retrieve(self, *, dataset_id: str, query: str, top_k: int) -> tuple[list[dict[str, Any]], float]:
        retrieval_model = build_dify_retrieval_model(
            self._retrieval_models.get(dataset_id),
            top_k,
        )
        started = time.perf_counter()
        payload = request_dify_json(
            "POST",
            dify_api_url(self.base_url, f"/datasets/{parse.quote(dataset_id, safe='')}/retrieve"),
            token=self.token,
            body={"query": query, "retrieval_model": retrieval_model},
            timeout=self.timeout,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        return records_from_dify_payload(payload), elapsed_ms


__all__ = [
    "DifyClient",
    "build_dify_retrieval_model",
    "dify_api_base",
    "dify_api_url",
    "normalize_dify_knowledge_base",
    "normalize_dify_record",
    "records_from_dify_payload",
    "request_dify_json",
]
