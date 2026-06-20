"""Log sanitization filter — redacts PII and sensitive data from log output.

Patterns redacted:
- Phone numbers (+998xxxxxxxxx and variants)
- Telegram session tokens / API keys
- JWT tokens (Bearer xxx.yyy.zzz)
- Email addresses
- Password fields in JSON-like strings
- Credit card numbers (basic 16-digit pattern)
"""

import logging
import re


# Compiled patterns for performance
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Phone numbers: +998901234567 → +998***4567
    (re.compile(r"\+?998\d{5}(\d{4})"), r"+998***\1"),
    # Generic international phone: +1234567890 → +***7890
    (re.compile(r"\+\d{7,}(\d{4})"), r"+***\1"),
    # JWT tokens: eyJhb... → [REDACTED_JWT]
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "[REDACTED_JWT]"),
    # Bearer tokens
    (re.compile(r"Bearer\s+[A-Za-z0-9._-]+"), "Bearer [REDACTED]"),
    # API keys (generic hex/base64 patterns > 20 chars after common key prefixes)
    (re.compile(r"(api[_-]?key|secret|token|password|auth)\s*[=:]\s*['\"]?([A-Za-z0-9_/+=.-]{20,})", re.IGNORECASE),
     r"\1=[REDACTED]"),
    # Email addresses
    (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), "[REDACTED_EMAIL]"),
    # Password fields in JSON
    (re.compile(r'("password"\s*:\s*)"[^"]*"', re.IGNORECASE), r'\1"[REDACTED]"'),
    (re.compile(r"(password_hash\s*=\s*)['\"][^'\"]*['\"]", re.IGNORECASE), r"\1[REDACTED]"),
    # Credit card numbers (16 digits, with optional separators)
    (re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"), "[REDACTED_CC]"),
    # Telegram session strings (base64-encoded, typically long)
    (re.compile(r"(session[_-]?string\s*[=:]\s*)['\"]?[A-Za-z0-9+/=]{50,}", re.IGNORECASE),
     r"\1[REDACTED_SESSION]"),
]


class SanitizingFilter(logging.Filter):
    """Logging filter that redacts sensitive data from log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = sanitize(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: sanitize(str(v)) if isinstance(v, str) else v for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(sanitize(str(a)) if isinstance(a, str) else a for a in record.args)
        return True


def sanitize(text: str) -> str:
    """Apply all redaction patterns to a string."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def install_sanitizer() -> None:
    """Install the sanitizing filter on the root logger."""
    root = logging.getLogger()
    root.addFilter(SanitizingFilter())
