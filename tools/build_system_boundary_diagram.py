"""Render docs/system_boundary_diagram.png in the style of the reference
architecture (dark-navy shapes, white labels, yellow today/tomorrow tags) —
populated with the layers this repo ACTUALLY ships per TDD_v3.0.

Layout matches the as-built three-phase pipeline in services/rag.py:

  - Left column   = INSIDE Ignition (Perspective + gateway Jython)
  - Center        = Phase 1 (pre-LLM, owns DB session)
  - Top right     = Phase 2 (LLM call, no DB held) + tool layer + RCA
  - Bottom strip  = Phase 3 (persist) + PostgreSQL + scheduler
  - The wire between Ignition and the service is labeled with the
    contract (CuratedContextPackage in, structured response out).
"""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse, FancyArrowPatch, FancyBboxPatch, Rectangle

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "system_boundary_diagram.png"

NAVY = "#1f4368"
NAVY_DARK = "#163252"
NAVY_LIGHT = "#2b5a87"
GREEN = "#2d5a3d"
GREEN_LIGHT = "#3a6f4a"
ORANGE = "#8a4a1f"
PURPLE = "#5a2d5a"
YELLOW = "#f4c430"
WHITE = "#ffffff"
GREY_BG = "#f7f7f7"
WIRE = "#2a4a6b"


def rounded(ax, x, y, w, h, color=NAVY):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.02,rounding_size=0.18",
        linewidth=0, facecolor=color,
    ))


def cylinder(ax, x, y, w, h, color=NAVY):
    ax.add_patch(Rectangle((x, y), w, h, facecolor=color, linewidth=0))
    ax.add_patch(Ellipse((x + w / 2, y), w, h * 0.18, facecolor=color, linewidth=0))
    ax.add_patch(Ellipse((x + w / 2, y + h), w, h * 0.18,
                         facecolor=NAVY_DARK, linewidth=0))


def label(ax, cx, top_y, title, lines=(), today=None, tomorrow=None,
          title_size=9.5, body_size=7.2, accent_size=7.0):
    ax.text(cx, top_y, title, ha="center", va="top",
            color=WHITE, fontsize=title_size, fontweight="bold")
    y = top_y - 0.30
    for ln in lines:
        ax.text(cx, y, ln, ha="center", va="top", color=WHITE, fontsize=body_size)
        y -= 0.24
    if today:
        y -= 0.06
        ax.text(cx, y, f"Today: {today}", ha="center", va="top",
                color=YELLOW, fontsize=accent_size, fontweight="bold")
        y -= 0.24
    if tomorrow:
        ax.text(cx, y, f"Tomorrow: {tomorrow}", ha="center", va="top",
                color=YELLOW, fontsize=accent_size, fontweight="bold")


def arrow(ax, p1, p2, double=False, dashed=False, color=WIRE, lw=1.6):
    style = "<->" if double else "->"
    ls = (0, (4, 3)) if dashed else "-"
    ax.add_patch(FancyArrowPatch(
        p1, p2, arrowstyle=style, mutation_scale=14,
        color=color, linewidth=lw, linestyle=ls,
        shrinkA=4, shrinkB=4,
    ))


def boundary(ax, x, y, w, h, edge, title):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.02,rounding_size=0.20",
        linewidth=1.8, edgecolor=edge, facecolor="none",
        linestyle=(0, (6, 4)),
    ))
    # Title sits ABOVE the dashed frame so it cannot collide with the
    # topmost shapes inside.
    ax.text(x + 0.18, y + h + 0.18, title, ha="left", va="bottom",
            color=edge, fontsize=10, fontweight="bold")


def build():
    fig, ax = plt.subplots(figsize=(20, 15))
    ax.set_xlim(0, 22)
    ax.set_ylim(-1.0, 16)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.patch.set_facecolor(GREY_BG)

    ax.text(11, 15.65,
            "IgnitionChatbot — System Boundary & Encapsulation (as-built v3.0)",
            ha="center", va="center", fontsize=14, fontweight="bold", color="#1a1a1a")

    # OUTER BOUNDARIES (titles sit ABOVE the shapes inside)
    boundary(ax, 0.3, 0.4, 6.4, 14.4, NAVY,
             "INSIDE IGNITION 8.1 GATEWAY  (Jython 2.7, read-only)")
    boundary(ax, 7.0, 0.4, 14.7, 14.4, GREEN,
             "OUTSIDE IGNITION  —  FastAPI service · PostgreSQL · ML")

    # ==================================================================
    # IGNITION SIDE
    # ==================================================================
    cylinder(ax, 0.9, 11.3, 5.2, 2.3)
    label(ax, 3.5, 13.45, "Real-Time Plant Context",
          ["PLC tags · system.tag.readBlocking",
           "tag historian aggregates",
           "active alarms · recipe · setpoints",
           "Perspective IdP user identity"],
          today="Ignition APIs (native)")

    rounded(ax, 0.9, 8.6, 5.2, 2.3)
    label(ax, 3.5, 10.75, "ai.context — Curated Context Builder",
          ["buildCuratedContext (Jython)",
           "tier-1 always + tier-2 routed (KEY_TAGS)",
           "deviation vs. window mean per tag class",
           "ISO-UTC timestamps · CuratedContextPackage",
           "Pydantic extra=forbid at boundary"],
          today="Hardcoded KEY_TAGS list",
          tomorrow="Discovered tag_registry")

    rounded(ax, 0.9, 6.2, 5.2, 2.0)
    label(ax, 3.5, 8.05, "ai.client — HTTPS Client",
          ["system.net.httpClient",
           "X-API-Key  +  Bearer JWT (HS256)",
           "HMAC-SHA256, TTL ≤ 120s, per request"])

    rounded(ax, 0.9, 3.4, 5.2, 2.4)
    label(ax, 3.5, 5.65, "Perspective ChatView",
          ["onActionPerformed → ai.client.sendQuery",
           "renders body + [N] citations",
           "color-coded confidence labels",
           "feedback / correction / outcome buttons"],
          today="Perspective view + Jython",
          tomorrow="HMI · web · mobile")

    rounded(ax, 0.9, 1.0, 5.2, 2.0, color=NAVY_LIGHT)
    label(ax, 3.5, 2.85, "Auto-trigger Templates  (gateway_wiring.py)",
          ["B13: alarm-triggered chat",
           "A5: defect-event triggered chat",
           "A6: shift-handoff brief"],
          today="Templates only — operator wires per INSTALL.md")

    # ==================================================================
    # SERVICE SIDE
    # ==================================================================
    rounded(ax, 7.3, 12.7, 14.1, 1.0, color=NAVY_DARK)
    ax.text(14.35, 13.45,
            "FastAPI routers  ·  /api/chat  /api/feedback  /api/corrections  /api/outcomes  /api/health",
            ha="center", va="center", color=WHITE, fontsize=9, fontweight="bold")
    ax.text(14.35, 13.05,
            "deps.require_api_key  +  require_attributed_user (JWT verify, _PERMISSIONS_CACHE)  +  slowapi rate-limit (chat_user_key)",
            ha="center", va="center", color=YELLOW, fontsize=7.4)

    # ---- Phase 1 ----
    rounded(ax, 7.3, 7.3, 6.7, 5.2, color=NAVY)
    ax.text(10.65, 12.30, "Phase 1 — Pre-LLM   (owns DB session)",
            ha="center", va="top", color=YELLOW, fontsize=9.5, fontweight="bold")

    rounded(ax, 7.55, 11.0, 3.05, 1.05, color=NAVY_LIGHT)
    label(ax, 9.07, 11.95, "anchor.py",
          ["parse anchor (past/current/pattern)",
           "control-verb refusal short-circuit",
           "clarification-first if ambiguous"],
          title_size=8.2, body_size=6.6)

    rounded(ax, 10.7, 11.0, 3.05, 1.05, color=NAVY_LIGHT)
    label(ax, 12.22, 11.95, "tag_selector + context_assembler",
          ["tier-1 + tier-2 routed tags",
           "14 prompt sections (A–N)",
           "[NOT APPLICABLE] for excluded"],
          title_size=8.2, body_size=6.6)

    rounded(ax, 7.55, 9.6, 6.2, 1.25, color=NAVY_LIGHT)
    ax.text(10.65, 10.75, "Hybrid Retrieval  (services/retrieval.py)",
            ha="center", va="top", color=WHITE, fontsize=8.4, fontweight="bold")
    ax.text(10.65, 10.42,
            "1. vector ANN  →  2. BM25  →  3. RRF (k=60)  →  4. FM/equip boost (1.5×/1.3×)  →  5. MMR (λ=0.7)  →  6. ±30% quality re-rank",
            ha="center", va="top", color=WHITE, fontsize=6.6)
    ax.text(10.65, 10.12,
            "+ failure-mode-matched history (separate structured-SQL path, not RRF-fused)",
            ha="center", va="top", color=YELLOW, fontsize=6.5)

    rounded(ax, 7.55, 8.45, 3.05, 1.0, color=NAVY_LIGHT)
    label(ax, 9.07, 9.35, "rules.py + line_memory",
          ["YAML deterministic rules",
           "approved memory (1.5× boost)"],
          title_size=8.2, body_size=6.6)

    rounded(ax, 10.7, 8.45, 3.05, 1.0, color=NAVY_LIGHT)
    label(ax, 12.22, 9.35, "change_ledger · anomaly · percentiles",
          ["TagDelta / RecipeDelta / CrewDelta",
           "Mahalanobis (live) · Page-Hinkley drift"],
          title_size=8.2, body_size=6.6)

    rounded(ax, 7.55, 7.5, 6.2, 0.85, color=ORANGE)
    ax.text(10.65, 8.10,
            "is_evidence_insufficient → templated refusal (no LLM call, audit logged)",
            ha="center", va="top", color=WHITE, fontsize=7.8, fontweight="bold")

    # ---- Phase 2 ----
    rounded(ax, 14.2, 7.3, 7.2, 5.2, color=GREEN)
    ax.text(17.8, 12.30, "Phase 2 — LLM   (no DB session held)",
            ha="center", va="top", color=YELLOW, fontsize=9.5, fontweight="bold")

    rounded(ax, 14.45, 11.0, 6.7, 1.05, color=GREEN_LIGHT)
    label(ax, 17.8, 11.95, "response_parser",
          ["parse confidence label + [N] citations",
           "downgrade-on-no-citation guardrail",
           "extract_cited_ids → filter sources"],
          title_size=8.2, body_size=6.6)

    rounded(ax, 14.45, 9.55, 3.25, 1.3, color=GREEN_LIGHT)
    ax.text(16.07, 10.75, "Tool Layer  (tools.py)",
            ha="center", va="top", color=WHITE, fontsize=8.4, fontweight="bold")
    ax.text(16.07, 10.45, "5 deterministic tools, 5s SQL timeout,",
            ha="center", va="top", color=WHITE, fontsize=6.6)
    ax.text(16.07, 10.20, "auto-citation, bounded result rows",
            ha="center", va="top", color=WHITE, fontsize=6.6)
    ax.text(16.07, 9.92, "percentile · compare · nearest · drift · defects",
            ha="center", va="top", color=YELLOW, fontsize=6.4)

    rounded(ax, 17.85, 9.55, 3.30, 1.3, color=GREEN_LIGHT)
    ax.text(19.5, 10.75, "Two-Step RCA Chain  (rca.py)",
            ha="center", va="top", color=WHITE, fontsize=8.4, fontweight="bold")
    ax.text(19.5, 10.45, "step1 hypothesise → tools → step2 adjudicate",
            ha="center", va="top", color=WHITE, fontsize=6.6)
    ax.text(19.5, 10.20, "shared 15-call budget · 5-min step1 TTL cache",
            ha="center", va="top", color=WHITE, fontsize=6.6)
    ax.text(19.5, 9.92, "fires on past_event + causal-intent only",
            ha="center", va="top", color=YELLOW, fontsize=6.4)

    rounded(ax, 14.45, 7.5, 6.7, 1.85, color=GREEN_LIGHT)
    ax.text(17.8, 9.20, "llm.py  —  _run_tool_loop  (provider-agnostic)",
            ha="center", va="top", color=WHITE, fontsize=8.4, fontweight="bold")
    ax.text(17.8, 8.85,
            "OpenAI  ·  Azure OpenAI  ·  Local (vLLM / llama.cpp / LM Studio)",
            ha="center", va="top", color=WHITE, fontsize=7.0)
    ax.text(17.8, 8.55,
            "selected by LLM_PROVIDER env  —  tool-call API surface identical",
            ha="center", va="top", color=WHITE, fontsize=6.7)
    ax.text(17.8, 8.25,
            "asyncio.Semaphore(llm_concurrency=4)  ·  in-process embeddings (MiniLM-L6-v2, 384-dim)",
            ha="center", va="top", color=YELLOW, fontsize=6.5)
    ax.text(17.8, 7.95, "Today: localized   ·   Tomorrow: hybrid / hosted",
            ha="center", va="top", color=YELLOW, fontsize=6.8, fontweight="bold")

    # ---- Phase 3 ----
    rounded(ax, 7.3, 5.6, 14.1, 1.5, color=NAVY)
    ax.text(14.35, 6.95, "Phase 3 — Persist   (new DB session)",
            ha="center", va="top", color=YELLOW, fontsize=9.5, fontweight="bold")
    ax.text(14.35, 6.60,
            "messages row (full context_snapshot, rca_summary, tool_calls)  ·  audit_log row (SHA-256 hash chain)  ·  feedback / corrections / outcomes intake",
            ha="center", va="top", color=WHITE, fontsize=7.3)
    ax.text(14.35, 6.30,
            "audit_log_immutable trigger blocks UPDATE/DELETE at DB layer  ·  prompt_version recorded for per-version A/B",
            ha="center", va="top", color=YELLOW, fontsize=6.7)

    # PostgreSQL
    cylinder(ax, 7.3, 1.0, 6.5, 4.0, color=ORANGE)
    label(ax, 10.55, 4.85, "PostgreSQL 16 + pgvector",
          ["30 tables across 9 schema groups",
           "documents · chunks · chunk_quality_signals",
           "production_runs · defect_events (FK→failure_modes)",
           "conversations · messages  ·  audit_log",
           "line_memory · memory_candidates · outcome_linkages",
           "feature_snapshots · ml_models · tag_registry (scaffold)",
           "pg_partman monthly partitions (24-mo hot retention)",
           "ivfflat now → hnsw at >250K chunks (migration 003)",
           "v_rca_precision_daily  ·  v_chat_perf_daily  (matviews)"],
          today="27-table v2 spec → 30 tables shipped",
          title_size=9.5, body_size=6.6, accent_size=6.7)

    # Scheduler / async
    rounded(ax, 14.2, 1.0, 7.2, 4.0, color=PURPLE)
    label(ax, 17.8, 4.85, "Async / Scheduled  (main.py lifespan)",
          ["nightly outcome_closure (cron 0 4 * * *):",
           "  sweep last 24h → outcome_linkages → refresh matview",
           "4-hourly anomaly model re-fit (90-day baseline)",
           "WO sync (read-only Ignition WO database)",
           "Symphony video capture adapter — STUB",
           "ingestion: chunker → embeddings → document_chunks",
           "engineer-mediated memory candidate review",
           "structlog (JSON) · Prometheus /metrics · Postgres logs"],
          today="In-process APScheduler",
          tomorrow="Dedicated worker container",
          title_size=9.5, body_size=6.6, accent_size=6.7)

    # ----- arrows: Ignition internal -----
    arrow(ax, (3.5, 11.3), (3.5, 10.9))
    arrow(ax, (3.5, 8.6), (3.5, 8.2), double=True)
    arrow(ax, (3.5, 6.2), (3.5, 5.8), double=True)
    arrow(ax, (3.5, 3.4), (3.5, 3.0), double=True)

    # ----- the wire (boundary crossing) -----
    # L-shaped: straight up from ai.client along the boundary gap,
    # then right into the routers strip, so it doesn't clip Phase 1 text.
    arrow(ax, (6.55, 7.2), (6.55, 13.2), double=True, lw=2.4, color=NAVY_DARK)
    arrow(ax, (6.55, 13.2), (7.3, 13.2), double=False, lw=2.4, color=NAVY_DARK)
    # Contract callout sits BELOW both boundaries on the boundary line
    # so it cannot collide with any internal shape.
    ax.text(11, -0.55,
            "BOUNDARY CONTRACT   —   HTTPS  ·  X-API-Key + Bearer JWT (HMAC-SHA256, TTL≤120s)\n"
            "Ignition → Service:  CuratedContextPackage  +  query  +  user/session/lineId      "
            "Service → Ignition:  body  ·  sources[]  ·  confidence  ·  message_id  ·  processing_ms",
            ha="center", va="center", fontsize=8.0, color=NAVY_DARK,
            bbox=dict(boxstyle="round,pad=0.45", facecolor="#fff8d8",
                      edgecolor=YELLOW, linewidth=1.4))

    # ----- service internal flow -----
    arrow(ax, (10.65, 12.7), (10.65, 12.50))
    arrow(ax, (9.07, 11.0), (9.07, 10.85))
    arrow(ax, (12.22, 11.0), (12.22, 10.85))
    arrow(ax, (10.65, 9.6), (10.65, 9.45))
    arrow(ax, (10.65, 8.45), (10.65, 8.35))
    arrow(ax, (14.0, 9.5), (14.45, 9.5), double=True, lw=2.0, color=GREEN)
    arrow(ax, (16.07, 9.55), (16.07, 9.35), double=True)
    arrow(ax, (19.5, 9.55), (19.5, 9.35), double=True)
    arrow(ax, (17.8, 9.35), (17.8, 11.0), double=True)
    arrow(ax, (10.65, 7.30), (10.65, 7.10), color=NAVY_DARK)
    arrow(ax, (17.8, 7.50), (17.8, 7.10), color=NAVY_DARK)
    arrow(ax, (10.55, 5.60), (10.55, 5.00), double=True, color=ORANGE, lw=2.0)
    arrow(ax, (8.5, 8.45), (8.5, 5.00), double=True, color=ORANGE)
    arrow(ax, (12.5, 8.45), (12.5, 5.00), double=True, color=ORANGE)
    arrow(ax, (15.5, 9.55), (13.0, 5.00), double=True, color=ORANGE, dashed=True)
    arrow(ax, (14.2, 3.0), (13.8, 3.0), double=True, color=PURPLE, lw=2.0)

    fig.savefig(OUT, dpi=170, bbox_inches="tight", facecolor=GREY_BG)
    plt.close(fig)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    build()
