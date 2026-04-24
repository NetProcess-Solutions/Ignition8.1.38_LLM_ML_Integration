"""Concatenate tools/build_tdd/content/*.md into docs/TDD_v3.0.md.

Usage:
    python tools/build_tdd/concat_md.py

Then in VS Code: open docs/TDD_v3.0.md, right-click in editor →
"Markdown PDF: Export (pdf)".
"""
from pathlib import Path

HERE = Path(__file__).parent
CONTENT = HERE / "content"
OUT = HERE.parent.parent / "docs" / "TDD_v3.0.md"

HEADER = """---
title: "Coater 1 Intelligent Operations Advisor — Technical Design Document v3.0"
author: "Jordan Taylor"
date: "April 2026"
---

<style>
  body { font-family: Calibri, Helvetica, Arial, sans-serif; font-size: 10.5pt; line-height: 1.4; color: #1a1a1a; }
  h1 { color: #C8102E; font-size: 22pt; border-bottom: 2px solid #C8102E; padding-bottom: 0.15em; page-break-before: always; }
  h1:first-of-type { page-break-before: avoid; }
  h2 { color: #C8102E; font-size: 14pt; }
  h3 { color: #1F3A5F; font-size: 12pt; }
  h4 { color: #1F3A5F; font-size: 10.8pt; }
  table { width: 100%; border-collapse: collapse; font-size: 9.5pt; margin: 0.6em 0; }
  th { background: #C8102E; color: #fff; padding: 5px 8px; text-align: left; }
  td { border: 1px solid #d8d8d8; padding: 5px 8px; vertical-align: top; }
  tr:nth-child(even) td { background: #f7f9fc; }
  code { background: #f4f4f4; padding: 0 3px; border-radius: 2px; font-family: Consolas, monospace; font-size: 9.2pt; }
  pre { background: #f4f4f4; border-left: 3px solid #1F3A5F; padding: 0.55em 0.7em; font-size: 8.8pt; line-height: 1.3; }
  pre code { background: none; padding: 0; }
  .delta-box { border: 1px solid #1F3A5F; border-left: 5px solid #C8102E; background: #f9fbff; padding: 0.6em 0.85em; margin: 0.9em 0; font-size: 9.8pt; }
  .delta-box .delta-title { color: #C8102E; font-weight: bold; font-size: 10.2pt; letter-spacing: 0.5px; margin: 0 0 0.35em 0; text-transform: uppercase; }
  .delta-box .label { display: inline-block; min-width: 6.5em; font-weight: bold; color: #1F3A5F; }
  .status-shipped { background: #1F7A1F; color: #fff; padding: 1px 7px; border-radius: 8px; font-size: 8.6pt; font-weight: bold; }
  .status-deferred { background: #777; color: #fff; padding: 1px 7px; border-radius: 8px; font-size: 8.6pt; font-weight: bold; }
  .status-stub { background: #B07A00; color: #fff; padding: 1px 7px; border-radius: 8px; font-size: 8.6pt; font-weight: bold; }
  .status-considering { background: #1F3A5F; color: #fff; padding: 1px 7px; border-radius: 8px; font-size: 8.6pt; font-weight: bold; }
</style>

# Coater 1 Intelligent Operations Advisor

**Technical Design Document — v3.0 As-Built Reference**

*Jordan Taylor · Process Engineer, Finishing & Coating · Shaw Industries Plant 4 (F0004), Dalton, GA*

*April 2026*

---

"""


def main() -> None:
    files = sorted(CONTENT.glob("*.md"))
    if not files:
        raise SystemExit(f"no chapters in {CONTENT}")
    out = [HEADER]
    for p in files:
        out.append(p.read_text(encoding="utf-8").rstrip() + "\n\n")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(out), encoding="utf-8")
    print(f"wrote {OUT}  ({sum(p.stat().st_size for p in files)} bytes from {len(files)} chapters)")


if __name__ == "__main__":
    main()
