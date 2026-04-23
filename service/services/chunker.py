"""Pure-Python text chunker. No heavy dependencies."""
from __future__ import annotations

CHUNK_TARGET_TOKENS = 500
CHUNK_OVERLAP_TOKENS = 50
APPROX_CHARS_PER_TOKEN = 4


def chunk_text(s: str) -> list[str]:
    s = s.strip()
    if not s:
        return []
    target = CHUNK_TARGET_TOKENS * APPROX_CHARS_PER_TOKEN
    overlap = CHUNK_OVERLAP_TOKENS * APPROX_CHARS_PER_TOKEN
    if len(s) <= target:
        return [s]
    chunks: list[str] = []
    start = 0
    while start < len(s):
        end = min(start + target, len(s))
        if end < len(s):
            for sep in (". ", ".\n", "\n\n"):
                idx = s.rfind(sep, start + target // 2, end)
                if idx != -1:
                    end = idx + len(sep)
                    break
        chunks.append(s[start:end].strip())
        if end >= len(s):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]
