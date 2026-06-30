"""Async Dify Knowledge Base API client for the concurrent runner."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable
from urllib import parse

import httpx

from kb_eval.dify_client import (
    build_dify_retrieval_model,
    dify_api_url,
    normalize_dify_knowledge_base,
    records_from_dify_payload,
)
from kb_eval.errors import EvalError
from kb_eval.retrieval_utils import (
    dataset_name_matches,
    knowledge_base_name,
    score_dataset,
    unwrap_data,
    unwrap_meta,
)


def _wrap_httpx_exception(url: str, exc: BaseException) -> EvalError:
    if isinstance(exc, httpx.HTTPStatusError):
        wrapped = EvalError(f"HTTP {exc.response.status_code} for Dify at {url}: {exc.response.text[:200]!r}")
        wrapped.__cause__ = exc
        return wrapped
    if isinstance(exc, httpx.TimeoutException):
        wrapped = EvalError(f"Timeout reaching Dify at {url}")
        wrapped.__cause__ = exc
        return wrapped
    if isinstance(exc, httpx.ConnectError):
        wrapped = EvalError(f"Cannot reach Dify at {url}: {exc}")
        wrapped.__cause__ = exc
        return wrapped
    if isinstance(exc, httpx.RequestError):
        wrapped = EvalError(f"Dify request failed for {url}: {exc}")
        wrapped.__cause__ = exc
        return wrapped
    wrapped = EvalError(f"Unexpected error calling Dify at {url}: {exc!r}")
    wrapped.__cause__ = exc
    return wrapped


async def _request_dify_json_async(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    token: str = "",
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = await client.request(method.upper(), url, json=body, headers=headers)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise _wrap_httpx_exception(url, exc) from exc
    if not response.content:
        return {}
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise EvalError(f"Invalid JSON response from Dify at {url}: {response.text[:200]!r}") from exc
    if isinstance(payload, dict) and payload.get("code") not in (None, 200, "success"):
        raise EvalError(f"Dify error for {url}: {payload.get('message') or payload}")
    if not isinstance(payload, dict):
        raise EvalError(f"Unexpected JSON response from Dify at {url}: {type(payload).__name__}")
    return payload


class AsyncDifyClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: str = "",
        timeout: float = 60,
        max_connections: int = 64,
        max_keepalive_connections: int = 8,
    ) -> None:
        if not token.strip():
            raise EvalError("Dify API key is required for direct connection")
        self.base_url = base_url
        self.token = token
        self.timeout = timeout
        self._max_connections = max_connections
        self._max_keepalive = max_keepalive_connections
        self._client: httpx.AsyncClient | None = None
        self._retrieval_models: dict[str, dict[str, Any]] = {}

    async def __aenter__(self) -> "AsyncDifyClient":
        self._client = httpx.AsyncClient(
            limits=httpx.Limits(
                max_connections=self._max_connections,
                max_keepalive_connections=self._max_keepalive,
            ),
            timeout=httpx.Timeout(connect=5.0, read=self.timeout, write=5.0, pool=5.0),
        )
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError(
                "AsyncDifyClient must be used as an async context manager "
                "(`async with AsyncDifyClient(...) as client:`)."
            )
        return self._client

    async def list_knowledge_bases(self) -> list[dict[str, Any]]:
        client = self._require_client()
        items: list[dict[str, Any]] = []
        page = 1
        limit = 100
        while True:
            payload = await _request_dify_json_async(
                client,
                "GET",
                dify_api_url(self.base_url, "/datasets", {"page": page, "limit": limit}),
                token=self.token,
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

    async def resolve_dataset_id(
        self,
        *,
        vendor: str,
        model: str,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        candidates = await self.list_knowledge_bases()
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

    async def validate_dataset_name(
        self,
        *,
        dataset_id: str,
        vendor: str,
        model: str,
        on_warning: Callable[[str], Any] | None = None,
    ) -> dict[str, Any] | None:
        expected_name = knowledge_base_name(vendor, model)
        if not expected_name:
            return None
        for item in await self.list_knowledge_bases():
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
                    result = on_warning(f"[WARN] {message}")
                    if isinstance(result, Awaitable):
                        await result
                    return item
                raise EvalError(
                    f"Dify knowledge base name {actual_name!r} for dataset_id={dataset_id!r} "
                    f"does not match expected {expected_name!r}.",
                )
            return item
        return None

    async def retrieve(
        self, *, dataset_id: str, query: str, top_k: int
    ) -> tuple[list[dict[str, Any]], float]:
        client = self._require_client()
        retrieval_model = build_dify_retrieval_model(
            self._retrieval_models.get(dataset_id),
            top_k,
        )
        loop = asyncio.get_running_loop()
        started = loop.time()
        payload = await _request_dify_json_async(
            client,
            "POST",
            dify_api_url(self.base_url, f"/datasets/{parse.quote(dataset_id, safe='')}/retrieve"),
            token=self.token,
            body={"query": query, "retrieval_model": retrieval_model},
        )
        elapsed_ms = (loop.time() - started) * 1000
        return records_from_dify_payload(payload), elapsed_ms


__all__ = [
    "AsyncDifyClient",
    "_request_dify_json_async",
    "_wrap_httpx_exception",
]
