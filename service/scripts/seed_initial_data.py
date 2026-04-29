"""
One-time / re-runnable seed script for prompts, rules, and starter line memory.

Run AFTER the database is up and the service container has booted (so the
embedding model is cached). Idempotent: uses upserts.

Usage:
    docker compose exec ai-service python -m scripts.seed_initial_data
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

import yaml
from sqlalchemy import text

# allow running both `python -m scripts.seed_initial_data` and `python scripts/seed_initial_data.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import SessionFactory  # noqa: E402
from services.embeddings import embed_sync  # noqa: E402

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "config" / "prompts"
RULES_FILE = Path(__file__).resolve().parent.parent / "config" / "rules" / "coater1_rules.yaml"


PROMPTS_TO_SEED = [
    {
        "name": "system_prompt",
        "version": "v1",
        "file": PROMPTS_DIR / "system_prompt_v1.txt",
        "notes": "Initial RAG-grounded system prompt with anti-hallucination rules",
        "activate": False,
    },
    {
        "name": "system_prompt",
        "version": "v2",
        "file": PROMPTS_DIR / "system_prompt_v2.txt",
        "notes": (
            "v2 grounding-first doctrine: parsed anchor, conditional buckets, "
            "narrowed refusal, full v2.0 citation provenance taxonomy."
        ),
        "activate": True,
    },
]

# Initial line memories.
#
# Deliberately empty.
#
# The previous seed values ("3 thermal zones", "200-250 fpm standard",
# "Style-A/Style-B", a generic delam checklist) were placeholder fiction
# that did not reflect Coater 1 reality (15 oven zones, zones 2 and 3 are
# the screwbar-drag concern, line-speed spec is per-style and lives in
# UNS dataset tags, real style codes come from Ignition at query time).
#
# Seeding fabricated "facts" into line_memory turns hallucinations into
# DB-blessed citations the chatbot will quote with a confidence label.
# That is the worst class of error the system can produce.
#
# Real line memory enters via the operator -> feedback -> memory candidate
# -> engineer approval flow (see services/feedback/ + line_memory.status).
# Do NOT re-populate this list without engineer-verified content.
INITIAL_MEMORIES: list[dict] = []


async def seed_prompts(session) -> None:
    for p in PROMPTS_TO_SEED:
        path: Path = p["file"]
        if not path.exists():
            print(f"  ! prompt file missing: {path}")
            continue
        content = path.read_text(encoding="utf-8")
        activate = bool(p.get("activate", True))
        await session.execute(
            text(
                """
                INSERT INTO prompt_versions (prompt_name, version, content, is_active, activated_at, notes, created_by)
                VALUES (:name, :ver, :content, :active, CASE WHEN :active THEN NOW() ELSE NULL END, :notes, 'seed_script')
                ON CONFLICT (prompt_name, version) DO UPDATE
                  SET content = EXCLUDED.content,
                      notes = EXCLUDED.notes
                """
            ),
            {"name": p["name"], "ver": p["version"], "content": content,
             "notes": p["notes"], "active": activate},
        )
        if activate:
            # Deactivate other versions of this prompt
            await session.execute(
                text(
                    """
                    UPDATE prompt_versions SET is_active = FALSE
                    WHERE prompt_name = :name AND version <> :ver
                    """
                ),
                {"name": p["name"], "ver": p["version"]},
            )
        print(f"  + prompt seeded: {p['name']} {p['version']} (active={activate})")


async def seed_rules(session) -> None:
    if not RULES_FILE.exists():
        print(f"  ! rules file missing: {RULES_FILE}")
        return
    data = yaml.safe_load(RULES_FILE.read_text(encoding="utf-8")) or {}
    rules = data.get("rules", [])
    for r in rules:
        await session.execute(
            text(
                """
                INSERT INTO business_rules
                    (rule_name, line_id, condition, conclusion, severity, category, version, created_by)
                VALUES
                    (:name, :line, CAST(:cond AS jsonb), :concl, :sev, :cat, 'v1', 'seed_script')
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "name": r["rule_name"],
                "line": r["line_id"],
                "cond": json.dumps(r["condition"]),
                "concl": r["conclusion"].strip(),
                "sev": r.get("severity", "info"),
                "cat": r.get("category"),
            },
        )
        print(f"  + rule seeded: {r['rule_name']}")


async def seed_memory(session) -> None:
    contents = [m["content"] for m in INITIAL_MEMORIES]
    embeddings = embed_sync(contents)
    for m, vec in zip(INITIAL_MEMORIES, embeddings):
        # Skip if a memory with identical content already exists
        existing = (await session.execute(
            text("SELECT id FROM line_memory WHERE content = :c"),
            {"c": m["content"]},
        )).first()
        if existing:
            print(f"  = memory exists, skipping: {m['category']}")
            continue
        mid = uuid.uuid4()
        vec_literal = "[" + ",".join(f"{v:.7f}" for v in vec) + "]"
        await session.execute(
            text(
                """
                INSERT INTO line_memory (
                    id, line_id, category, content, source, confidence, status,
                    embedding, tags, equipment_ids, applies_to_products,
                    created_by, approved_by, approved_date
                ) VALUES (
                    :id, 'coater1', :cat, :content, 'seed_script', 'medium', 'approved',
                    CAST(:vec AS vector), :tags, :eqs, :prods,
                    'seed_script', 'seed_script', NOW()
                )
                """
            ),
            {
                "id": mid,
                "cat": m["category"],
                "content": m["content"],
                "vec": vec_literal,
                "tags": m.get("tags", []),
                "eqs": m.get("equipment_ids", []),
                "prods": m.get("applies_to_products", []),
            },
        )
        print(f"  + memory seeded: {m['category']}")


async def seed_demo_user_profiles(session) -> None:
    users = [
        ("admin", "Admin User", "admin"),
    ]
    for uid, dn, role in users:
        await session.execute(
            text(
                """
                INSERT INTO user_profiles (id, display_name, role_primary, lines_primary)
                VALUES (:id, :dn, :role, ARRAY['coater1'])
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": uid, "dn": dn, "role": role},
        )
        print(f"  + user profile seeded: {uid}")


async def main() -> None:
    print("Seeding prompts...")
    async with SessionFactory() as s:
        await seed_prompts(s)
        await s.commit()

    print("Seeding rules...")
    async with SessionFactory() as s:
        await seed_rules(s)
        await s.commit()

    print("Seeding memory...")
    async with SessionFactory() as s:
        await seed_memory(s)
        await s.commit()

    print("Seeding demo user profiles...")
    async with SessionFactory() as s:
        await seed_demo_user_profiles(s)
        await s.commit()

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
