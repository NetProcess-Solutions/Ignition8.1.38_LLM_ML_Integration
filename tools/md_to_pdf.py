"""
Render any markdown file to a styled PDF.

Why this exists
---------------
The full TDD pipeline in ``tools/build_tdd`` is overkill for one-off
documents like the OT/IT briefing handout. This module is the minimum
viable converter: read one ``.md`` file, render it to a self-contained
HTML with light styling, then print it to PDF using whichever backend is
available.

Backends, in order of preference:
    1. WeasyPrint            (best output, requires GTK runtime)
    2. Edge / Chrome headless (--print-to-pdf, no extra runtime needed)

Usage::

    python -m tools.md_to_pdf docs/BRIEFING_HANDOUT.md
        # writes docs/BRIEFING_HANDOUT.pdf next to the source

    python -m tools.md_to_pdf docs/BRIEFING_HANDOUT.md --out somewhere.pdf
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown


_CSS = """
@page { size: Letter; margin: 0.6in 0.65in 0.7in 0.65in; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  font-family: 'Calibri', 'Segoe UI', Helvetica, Arial, sans-serif;
  font-size: 10.5pt; line-height: 1.4; color: #1a1a1a; margin: 0;
}
h1 { font-size: 18pt; color: #14365c; margin: 0 0 6pt 0; border-bottom: 2pt solid #14365c; padding-bottom: 4pt; }
h2 { font-size: 13pt; color: #14365c; margin: 14pt 0 4pt 0; border-bottom: 1pt solid #c8d3e0; padding-bottom: 2pt; }
h3 { font-size: 11pt; color: #1f4a80; margin: 10pt 0 3pt 0; }
p, li { margin: 3pt 0; }
ul, ol { margin: 3pt 0 6pt 0; padding-left: 22pt; }
code { font-family: 'Consolas', 'Courier New', monospace; font-size: 9.2pt;
       background: #f3f4f6; padding: 0 3pt; border-radius: 2pt; }
pre { font-family: 'Consolas', 'Courier New', monospace; font-size: 8.6pt;
      line-height: 1.25; background: #f3f4f6; border: 1pt solid #d8dde3;
      border-radius: 3pt; padding: 6pt 8pt; overflow-x: auto;
      page-break-inside: avoid; white-space: pre; }
blockquote { border-left: 3pt solid #14365c; background: #eef3fa;
             margin: 6pt 0; padding: 4pt 10pt; color: #2a2a2a; }
table { border-collapse: collapse; margin: 6pt 0; width: 100%;
        page-break-inside: avoid; font-size: 9.5pt; }
th, td { border: 1pt solid #c8d3e0; padding: 3pt 6pt; text-align: left;
         vertical-align: top; }
th { background: #14365c; color: #fff; font-weight: bold; }
tr:nth-child(even) td { background: #f7f9fc; }
hr { border: 0; border-top: 1pt solid #c8d3e0; margin: 10pt 0; }
strong { color: #14365c; }
a { color: #14365c; text-decoration: none; }
"""


_BROWSER_CANDIDATES = [
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def _render_html(md_path: Path) -> str:
    md_text = md_path.read_text(encoding="utf-8")
    body = markdown.markdown(
        md_text,
        extensions=["extra", "sane_lists", "toc"],
        extension_configs={"toc": {"permalink": False}},
        output_format="html5",
    )
    title = md_path.stem.replace("_", " ").title()
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title><style>{_CSS}</style>"
        "</head><body>" + body + "</body></html>"
    )


def _try_weasyprint(html_str: str, out_pdf: Path) -> bool:
    try:
        from weasyprint import HTML  # type: ignore
    except Exception as exc:
        print(f"[weasyprint] unavailable: {exc}", file=sys.stderr)
        return False
    try:
        HTML(string=html_str).write_pdf(str(out_pdf))
    except Exception as exc:  # pragma: no cover
        print(f"[weasyprint] render failed: {exc}", file=sys.stderr)
        return False
    print(f"wrote {out_pdf}  (backend: weasyprint)")
    return True


def _find_browser() -> str | None:
    for cand in _BROWSER_CANDIDATES:
        if Path(cand).is_file():
            return cand
    for name in ("msedge", "chrome", "chromium", "google-chrome"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _try_browser(html_str: str, out_pdf: Path) -> bool:
    browser = _find_browser()
    if not browser:
        print("[browser] no Chrome/Edge found on PATH", file=sys.stderr)
        return False

    out_pdf = out_pdf.resolve()
    with tempfile.TemporaryDirectory(prefix="md2pdf_") as tmp:
        html_path = Path(tmp) / "doc.html"
        html_path.write_text(html_str, encoding="utf-8")
        html_uri = "file:///" + str(html_path).replace("\\", "/")

        cmd = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--user-data-dir={tmp}\\profile",
            f"--print-to-pdf={out_pdf}",
            "--print-to-pdf-no-header",
            "--virtual-time-budget=8000",
            html_uri,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120
            )
        except Exception as exc:
            print(f"[browser] launch failed: {exc}", file=sys.stderr)
            return False

    if not out_pdf.is_file() or out_pdf.stat().st_size == 0:
        stderr = (result.stderr or "")[:500] if "result" in locals() else ""
        print(f"[browser] no PDF produced. stderr={stderr}", file=sys.stderr)
        return False
    print(f"wrote {out_pdf}  (backend: {Path(browser).name})")
    return True


def md_to_pdf(md_path: Path, out_pdf: Path | None = None) -> Path:
    if not md_path.is_file():
        raise SystemExit(f"input not found: {md_path}")
    if out_pdf is None:
        out_pdf = md_path.with_suffix(".pdf")

    html_str = _render_html(md_path)

    # Always also drop the rendered HTML next to the PDF for inspection.
    html_path = out_pdf.with_suffix(".html")
    html_path.write_text(html_str, encoding="utf-8")

    # Prefer markdown_pdf (pure-Python, reliable on Windows). Fall back to
    # WeasyPrint, then headless Edge/Chrome.
    try:
        from markdown_pdf import MarkdownPdf, Section  # type: ignore
        pdf = MarkdownPdf()
        pdf.add_section(Section(md_path.read_text(encoding="utf-8"), toc=False))
        pdf.save(str(out_pdf))
        print(f"wrote {out_pdf}  (backend: markdown_pdf)")
        return out_pdf
    except Exception as exc:
        print(f"[markdown_pdf] unavailable: {exc}", file=sys.stderr)

    if _try_weasyprint(html_str, out_pdf):
        return out_pdf
    if _try_browser(html_str, out_pdf):
        return out_pdf

    raise SystemExit(
        "No usable PDF backend. Install WeasyPrint with its GTK runtime, "
        "or install Microsoft Edge / Google Chrome and ensure it is on PATH."
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Render a markdown file to PDF.")
    p.add_argument("input", type=Path, help="path to .md file")
    p.add_argument("--out", type=Path, default=None,
                   help="output .pdf path (default: alongside input)")
    args = p.parse_args(argv)
    md_to_pdf(args.input, args.out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
