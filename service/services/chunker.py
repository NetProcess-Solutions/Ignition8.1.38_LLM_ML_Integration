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


# ---------------------------------------------------------------------------
# Sprint 6 / B3 — Structure-aware chunking.
#
# Detects markdown / loose-prose headings (`# foo`, `## foo`, `Foo:` on its
# own line in ALL CAPS), splits on them first, then falls back to the size-
# based `chunk_text` within each section. Each chunk is returned WITH a
# `section_path` (`["§1 Background", "§1.2 Symptoms"]`) and a stable
# `parent_heading_id` so the retriever can present "what section is this
# in?" without re-parsing.
# ---------------------------------------------------------------------------

import hashlib
import re
from dataclasses import dataclass, field


@dataclass
class StructuredChunk:
    text: str
    section_path: list[str] = field(default_factory=list)
    parent_heading_id: str | None = None
    order: int = 0

    def metadata(self) -> dict[str, object]:
        return {
            "section_path": list(self.section_path),
            "parent_heading_id": self.parent_heading_id,
        }


_HEADING_PATTERNS = (
    # Markdown ATX (# / ##), level encoded in group 1.
    re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$"),
    # Loose all-caps prose heading: "PROCEDURE:" or "STEP 1 — TEARDOWN"
    re.compile(r"^([A-Z][A-Z0-9 _\-/&]{2,80}):?\s*$"),
)


def _classify_heading(line: str) -> tuple[int, str] | None:
    stripped = line.rstrip()
    for i, pat in enumerate(_HEADING_PATTERNS):
        m = pat.match(stripped)
        if not m:
            continue
        if i == 0:
            return len(m.group(1)), m.group(2).strip()
        # All-caps fallback maps to a single mid-level heading.
        return 2, stripped.strip(" :")
    return None


def _heading_id(text: str, index: int) -> str:
    h = hashlib.sha1(f"{index}|{text}".encode("utf-8")).hexdigest()[:10]
    return f"h_{h}"


def chunk_structured(s: str) -> list[StructuredChunk]:
    """Split `s` on detected headings, then size-chunk each block.

    Returns one or more `StructuredChunk` objects in document order. The
    `section_path` field tracks the most-recent heading at each level so
    a chunk under "## Symptoms" inside "# Failure 4521" gets path
    ["Failure 4521", "Symptoms"]. Useful both for citation display and
    for retrieval-time boost ("user asked about teardown -> prefer
    chunks under a TEARDOWN heading").
    """
    s = (s or "").strip()
    if not s:
        return []

    lines = s.splitlines()
    sections: list[tuple[list[str], str | None, str]] = []
    cur_buf: list[str] = []
    cur_path: list[str] = []
    cur_hid: str | None = None
    level_stack: list[tuple[int, str]] = []  # (level, text)

    def _flush() -> None:
        body = "\n".join(cur_buf).strip()
        if body:
            sections.append((list(cur_path), cur_hid, body))
        cur_buf.clear()

    for line in lines:
        head = _classify_heading(line)
        if head is None:
            cur_buf.append(line)
            continue
        # A heading -> flush previous block.
        _flush()
        level, text = head
        # Pop deeper headings.
        while level_stack and level_stack[-1][0] >= level:
            level_stack.pop()
        level_stack.append((level, text))
        cur_path = [t for _, t in level_stack]
        cur_hid = _heading_id(" / ".join(cur_path), len(sections))
    _flush()

    # If no headings detected, fall back to the legacy chunker on the
    # whole document with empty section_path.
    if not sections:
        return [
            StructuredChunk(text=t, section_path=[], parent_heading_id=None, order=i)
            for i, t in enumerate(chunk_text(s))
        ]

    out: list[StructuredChunk] = []
    order = 0
    for path, hid, body in sections:
        for piece in chunk_text(body):
            out.append(StructuredChunk(
                text=piece, section_path=list(path),
                parent_heading_id=hid, order=order,
            ))
            order += 1
    return out
