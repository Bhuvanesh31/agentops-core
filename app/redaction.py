"""Secret redaction for raw event payloads.

Redaction runs before any payload is persisted. Two complementary strategies
are applied recursively across the whole JSON structure:

1. Key-based: any value stored under a key that names a credential
   (``password``, ``token``, ``authorization`` ...) is replaced wholesale.
2. Value-pattern: any string that *looks like* a known secret format
   (API keys, JWTs, PEM blocks, bearer headers ...) is replaced, regardless of
   the key it appears under -- this catches secrets pasted into free-text
   fields such as command lines or prompts.

The original payload is never mutated; a redacted copy is returned along with a
status of ``"redacted"`` (something was changed) or ``"clean"`` (nothing was).
"""

import re
from typing import Any

REDACTED = "[REDACTED]"

# Keys whose values are treated as sensitive regardless of content.
# Matched case-insensitively as substrings, so "api_key" also matches
# "openai_api_key", "x-api-key", etc.
SECRET_KEY_HINTS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "api-key",
    "authorization",
    "access_key",
    "secret_key",
    "private_key",
    "client_secret",
    "credential",
    "session_token",
    "refresh_token",
    "bearer",
)

# Value shapes that indicate a secret no matter which key holds them.
SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),  # Anthropic API key
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # OpenAI-style API key
    re.compile(r"gh[posru]_[A-Za-z0-9]{20,}"),  # GitHub token
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key id
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),  # Slack token
    re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),  # JWT
    re.compile(r"-----BEGIN[ A-Z]*PRIVATE KEY-----[\s\S]+?-----END[ A-Z]*PRIVATE KEY-----"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{10,}"),  # bearer header
)


def _key_is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in SECRET_KEY_HINTS)


def _redact_string(value: str) -> tuple[str, bool]:
    """Replace any secret-shaped substrings within a string."""
    changed = False
    for pattern in SECRET_VALUE_PATTERNS:
        new_value, count = pattern.subn(REDACTED, value)
        if count:
            changed = True
            value = new_value
    return value, changed


def _redact(value: Any) -> tuple[Any, bool]:
    """Recursively redact a JSON-compatible value. Returns (value, changed)."""
    if isinstance(value, dict):
        changed = False
        result: dict[str, Any] = {}
        for key, item in value.items():
            if _key_is_sensitive(str(key)) and item not in (None, "", {}, []):
                result[key] = REDACTED
                changed = True
            else:
                result[key], item_changed = _redact(item)
                changed = changed or item_changed
        return result, changed

    if isinstance(value, list):
        changed = False
        result_list = []
        for item in value:
            new_item, item_changed = _redact(item)
            result_list.append(new_item)
            changed = changed or item_changed
        return result_list, changed

    if isinstance(value, str):
        return _redact_string(value)

    return value, False


def redact_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Return a redacted copy of ``payload`` and a redaction status.

    Status is ``"redacted"`` if anything was changed, otherwise ``"clean"``.
    """
    redacted, changed = _redact(payload)
    return redacted, "redacted" if changed else "clean"
