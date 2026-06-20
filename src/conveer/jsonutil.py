"""Tolerant JSON extraction from model output (fenced / prose-wrapped)."""

from __future__ import annotations

import json
import re
from typing import Any


def strip_fences(text: str) -> str:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    return fence.group(1).strip() if fence else text


def extract_json(text: str, kind: str = "object") -> Any:
    """Extract a JSON array ('array') or object ('object') from text, tolerantly."""
    open_ch, close_ch = ("[", "]") if kind == "array" else ("{", "}")
    cleaned = strip_fences(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find(open_ch)
        end = cleaned.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise
