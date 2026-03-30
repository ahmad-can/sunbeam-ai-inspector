"""Secret and token masking for log content before LLM calls."""

from __future__ import annotations

import re

_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(sk-[A-Za-z0-9_-]{20,})"), "***OPENAI_KEY***"),
    (re.compile(r"(sk-ant-[A-Za-z0-9_-]{20,})"), "***ANTHROPIC_KEY***"),
    (re.compile(r"(ghp_[A-Za-z0-9]{36,})"), "***GITHUB_TOKEN***"),
    (re.compile(r"(ghs_[A-Za-z0-9]{36,})"), "***GITHUB_TOKEN***"),
    (re.compile(r"(Bearer\s+[A-Za-z0-9._\-]+)"), "Bearer ***REDACTED***"),
    (re.compile(r"(token\s*[:=]\s*)['\"]?[A-Za-z0-9._\-]{16,}['\"]?", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"(password\s*[:=]\s*)['\"]?[^\s'\"]{4,}['\"]?", re.IGNORECASE), r"\1***REDACTED***"),
    (re.compile(r"(VAULT_[A-Z_]*\s*[:=]\s*)\S+"), r"\1***REDACTED***"),
    (re.compile(r"(\*{3})"), "***"),
]


def sanitize(text: str) -> str:
    """Mask secrets and tokens in *text* before sending to an LLM."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def sanitize_events(events: list[dict]) -> list[dict]:
    """Return a shallow copy of *events* with messages sanitized."""
    out = []
    for ev in events:
        cleaned = dict(ev)
        if "message" in cleaned:
            cleaned["message"] = sanitize(cleaned["message"])
        out.append(cleaned)
    return out
