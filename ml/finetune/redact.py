"""
B5 — PII / secret redaction for fine-tuning corpus.

PLACEHOLDER.

The fine-tuning data path runs every assistant response through
`scrub()` before it lands in the JSONL. Errors here cause real privacy
leaks, so this module should ship with strong unit tests in
tests/test_redact.py covering at minimum:

  * Operator names (look up against employees table)
  * Badge numbers / employee IDs
  * Customer names from work-order text
  * Internal IP addresses, hostnames
  * Email addresses, phone numbers
"""
from __future__ import annotations

import re

# Trivial fallbacks. Replace with a real PII detector (e.g. presidio) +
# a project-specific entity list before going live.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b")
_BADGE_RE = re.compile(r"\bemp[_-]?\d{4,}\b", re.IGNORECASE)


def scrub(text: str, *, employee_names: set[str] | None = None) -> str:
    """Return `text` with PII / secrets replaced by stable placeholders."""
    if not text:
        return text
    out = _EMAIL_RE.sub("<EMAIL>", text)
    out = _PHONE_RE.sub("<PHONE>", out)
    out = _BADGE_RE.sub("<BADGE>", out)
    for name in employee_names or set():
        # Whole-word case-insensitive replace.
        out = re.sub(rf"\b{re.escape(name)}\b", "<OPERATOR>", out, flags=re.IGNORECASE)
    # TODO(B5): hook in presidio or equivalent here for catch-all.
    return out
