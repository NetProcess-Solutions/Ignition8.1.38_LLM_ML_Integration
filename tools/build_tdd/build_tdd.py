"""
Build the Coater 1 Technical Design Document v3.0 (as-built reference).

Pipeline:
    chapter markdown files in content/  (sorted by filename prefix)
        -> markdown -> HTML fragments
        -> rendered into templates/tdd.html.j2 with TOC
        -> always writes a single self-contained HTML
        -> WeasyPrint when available (preferred) OR Edge/Chrome --headless
           --print-to-pdf for environments without GTK runtime (Windows)

Run:
    pip install -r tools/build_tdd/requirements.txt
    python -m tools.build_tdd.build_tdd
        # writes docs/TDD_v3.0.html and docs/TDD_v3.0.pdf
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape


HERE = Path(__file__).parent
CONTENT_DIR = HERE / "content"
TEMPLATE_DIR = HERE / "templates"
ASSETS_DIR = HERE / "assets"
DEFAULT_OUT = HERE.parent.parent / "docs" / "TDD_v3.0.pdf"

MD_EXTENSIONS = [
    "extra",          # tables, fenced_code, attr_list, def_list
    "sane_lists",
    "toc",
    "codehilite",
]
MD_EXT_CONFIGS = {
    "toc": {"permalink": False, "toc_depth": "2-3"},
    "codehilite": {"guess_lang": False, "noclasses": True, "css_class": "code"},
}


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-")


def collect_chapters() -> list[dict]:
    chapters: list[dict] = []
    files = sorted(CONTENT_DIR.glob("*.md"))
    if not files:
        raise SystemExit(f"no chapter files found in {CONTENT_DIR}")
    for path in files:
        raw = path.read_text(encoding="utf-8")
        title = path.stem
        for line in raw.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        md = markdown.Markdown(
            extensions=MD_EXTENSIONS, extension_configs=MD_EXT_CONFIGS
        )
        html = md.convert(raw)
        chapters.append({
            "id": slugify(path.stem),
            "title": title,
            "html": html,
            "filename": path.name,
        })
    return chapters


def render_html(out_html: Path) -> str:
    """Render the combined HTML and write to disk. Returns the HTML string."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("tdd.html.j2")

    chapters = collect_chapters()
    toc = [{"id": c["id"], "title": c["title"]} for c in chapters]

    css_text = (TEMPLATE_DIR / "styles.css").read_text(encoding="utf-8")

    html_str = template.render(
        chapters=chapters,
        toc=toc,
        title="Coater 1 Intelligent Operations Advisor",
        subtitle="Technical Design Document — v3.0 As-Built Reference",
        author="Jordan Taylor",
        org="Shaw Industries — Plant 4 (F0004), Dalton, GA",
        date="April 2026",
        inline_css=css_text,
    )

    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(html_str, encoding="utf-8")
    print(f"wrote {out_html}")
    return html_str


# ---------------------------------------------------------------------------
# PDF backends
# ---------------------------------------------------------------------------
def _try_xhtml2pdf(html_str: str, out_pdf: Path) -> bool:
    """Pure-Python ReportLab-backed fallback. No native deps."""
    try:
        from xhtml2pdf import pisa  # noqa: WPS433
    except Exception as exc:  # pragma: no cover
        print(f"[xhtml2pdf] unavailable: {exc}", file=sys.stderr)
        return False
    try:
        with open(out_pdf, "wb") as fh:
            result = pisa.CreatePDF(
                src=html_str,
                dest=fh,
                encoding="utf-8",
                path=str(HERE),
            )
    except Exception as exc:  # pragma: no cover
        print(f"[xhtml2pdf] render failed: {exc}", file=sys.stderr)
        return False
    if result.err:
        print(f"[xhtml2pdf] {result.err} errors during render", file=sys.stderr)
        return False
    print(f"wrote {out_pdf}  (backend: xhtml2pdf/reportlab)")
    return True


def _try_weasyprint(html_str: str, out_pdf: Path) -> bool:
    try:
        from weasyprint import HTML, CSS  # noqa: WPS433
    except Exception as exc:  # pragma: no cover
        print(f"[weasyprint] unavailable: {exc}", file=sys.stderr)
        return False
    try:
        HTML(string=html_str, base_url=str(HERE)).write_pdf(
            target=str(out_pdf),
            stylesheets=[CSS(filename=str(TEMPLATE_DIR / "styles.css"))],
        )
    except Exception as exc:  # pragma: no cover
        print(f"[weasyprint] render failed: {exc}", file=sys.stderr)
        return False
    print(f"wrote {out_pdf}  (backend: weasyprint)")
    return True


_BROWSER_CANDIDATES = [
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def _find_browser() -> str | None:
    for cand in _BROWSER_CANDIDATES:
        if Path(cand).is_file():
            return cand
    for name in ("msedge", "chrome", "chromium", "google-chrome"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _try_browser(html_path: Path, out_pdf: Path) -> bool:
    browser = _find_browser()
    if not browser:
        print("[browser] no Chrome/Edge found", file=sys.stderr)
        return False

    out_pdf = out_pdf.resolve()
    html_uri = "file:///" + str(html_path.resolve()).replace("\\", "/")

    with tempfile.TemporaryDirectory(prefix="tdd_browser_") as tmp:
        cmd = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--user-data-dir={tmp}",
            f"--print-to-pdf={out_pdf}",
            "--print-to-pdf-no-header",
            "--virtual-time-budget=10000",
            html_uri,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=180
            )
        except Exception as exc:
            print(f"[browser] launch failed: {exc}", file=sys.stderr)
            return False

    if not out_pdf.is_file() or out_pdf.stat().st_size == 0:
        stderr = (result.stderr or "")[:500] if 'result' in locals() else ""
        print(f"[browser] no PDF produced. stderr={stderr}", file=sys.stderr)
        return False
    print(f"wrote {out_pdf}  (backend: {Path(browser).name})")
    return True


def render_pdf(out_pdf: Path) -> None:
    out_html = out_pdf.with_suffix(".html")
    html_str = render_html(out_html)

    if _try_weasyprint(html_str, out_pdf):
        return
    if _try_xhtml2pdf(html_str, out_pdf):
        return
    if _try_browser(out_html, out_pdf):
        return
    raise SystemExit(
        "Could not render PDF. WeasyPrint is unavailable (missing GTK runtime "
        "on Windows) and no Chrome/Edge was found for headless print. "
        "Open the HTML in a browser and use File > Print > Save as PDF."
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Build the v3.0 TDD PDF.")
    p.add_argument("--out", default=str(DEFAULT_OUT),
                   help="Output PDF path (default: docs/TDD_v3.0.pdf). "
                        "An HTML sibling is always written.")
    p.add_argument("--html-only", action="store_true",
                   help="Skip PDF rendering; only emit HTML.")
    args = p.parse_args()

    out_pdf = Path(args.out)
    if args.html_only:
        render_html(out_pdf.with_suffix(".html"))
        return
    render_pdf(out_pdf)


if __name__ == "__main__":
    main()
