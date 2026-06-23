"""Извлечение JSON из ответов LLM."""

from __future__ import annotations

import json
import re


def _strip_markdown_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)
    return text.strip()


def _balanced_slice(text: str, open_ch: str, close_ch: str) -> str | None:
    start = text.find(open_ch)
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def extract_json_object(text: str) -> dict | None:
    """Первый сбалансированный JSON-объект в тексте."""
    raw = _strip_markdown_fences(text)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    chunk = _balanced_slice(raw, "{", "}")
    if chunk:
        try:
            data = json.loads(chunk)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return None


def extract_json_array(text: str) -> list | None:
    """Первый сбалансированный JSON-массив в тексте."""
    raw = _strip_markdown_fences(text)
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass
    chunk = _balanced_slice(raw, "[", "]")
    if chunk:
        try:
            data = json.loads(chunk)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
    return None
