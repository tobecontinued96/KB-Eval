"""Shared retrieval matching helpers for Dify knowledge-base evaluation."""

from __future__ import annotations

import re
from typing import Any


def unwrap_data(payload: dict[str, Any]) -> Any:
    return payload.get("data") if "data" in payload else payload


def unwrap_meta(payload: dict[str, Any]) -> dict[str, Any]:
    meta = payload.get("meta")
    return meta if isinstance(meta, dict) else {}


def normalize_key(value: Any) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"mineru[_\-\s]*markdown", "", text)
    text = re.sub(r"\.(md|pdf|docx?|txt)$", "", text)
    text = re.sub(r"_?\d{12,}", "", text)
    text = re.sub(r"[\\/_\-\s,，。、：:；;()（）\[\]【】]+", "", text)
    return text


def text_contains(haystack: Any, needle: str) -> bool:
    h = normalize_key(haystack)
    n = normalize_key(needle)
    return bool(n and h and (n in h or h in n))


def vendor_aliases(vendor: str) -> set[str]:
    aliases = {vendor, vendor.casefold()}
    if vendor in {"华为", "Huawei", "HUAWEI"}:
        aliases.update({"华为", "huawei", "HUAWEI", "Huawei"})
    return {normalize_key(item) for item in aliases if item}


def knowledge_base_name(vendor: str, model: str) -> str:
    return " ".join(item for item in [vendor.strip(), model.strip()] if item)


def dataset_name_matches(item: dict[str, Any], expected_name: str) -> bool:
    values = [item.get("name"), item.get("display_name")]
    return any(text_contains(value, expected_name) for value in values)


def score_dataset(item: dict[str, Any], vendor: str, model: str) -> int:
    score = 0
    expected_name = knowledge_base_name(vendor, model)
    name_values = [item.get("name"), item.get("display_name")]
    vendor_values = [item.get("vendor"), item.get("name"), item.get("display_name"), item.get("description")]
    model_values = [item.get("model"), item.get("name"), item.get("display_name"), item.get("description")]
    if any(text_contains(value, expected_name) for value in name_values):
        score += 120
    aliases = vendor_aliases(vendor)
    if any(normalize_key(value) in aliases for value in vendor_values):
        score += 80
    elif any(any(alias in normalize_key(value) for alias in aliases) for value in vendor_values):
        score += 40
    if any(text_contains(value, model) for value in model_values):
        score += 80
    for document in item.get("documents") or []:
        name = str(document.get("name") or "")
        if any(alias in normalize_key(name) for alias in aliases):
            score += 5
        if text_contains(name, model):
            score += 10
    return score
