"""Deterministic FORM rules for text leaving the model (or replayed back to it).

A hard output rule like "no em-dashes" cannot be guaranteed by a prompt: the
model imitates the form it sees, including its own prior replies in the replay
window, so a single leaked em-dash teaches more. This enforces the rule
deterministically at the boundary instead.

v1's core rule: collapse an em-dash (U+2014) plus surrounding whitespace to a
comma (the house style hermes_reply already names: "Use a comma, a period, or a
new bubble where a dash would go"). It targets U+2014 ONLY, so hyphen-minus in
numbers (5-7, 10-15) and en-dashes (U+2013) are untouched. Two cleanup rules
run after it so the result reads naturally: a comma that now abuts an existing
comma is collapsed to one (no doubled comma when the source already had a comma
before the em-dash), and a leading-comma artifact at the very start is dropped
(a sentence that opened with an em-dash does not become a stray ", "). Idempotent.
"""

from __future__ import annotations

import re

# Ordered (pattern, replacement) rules. Extend here when a new hard form rule is
# agreed; keep each rule deterministic and idempotent.
_OUTGOING_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\s*—\s*"), ", "),   # em-dash -> comma separator
    (re.compile(r",\s*,"), ","),       # collapse a comma now abutting an existing comma (the trailing space is already in the stream)
    (re.compile(r"^\s*,\s*"), ""),     # drop a leading-comma artifact at the very start
)


def normalize_outgoing_text(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else ""
    for pattern, replacement in _OUTGOING_RULES:
        text = pattern.sub(replacement, text)
    return text
