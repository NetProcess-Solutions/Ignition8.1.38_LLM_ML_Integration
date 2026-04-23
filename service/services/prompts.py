"""Loads the active prompt and context template from prompt_versions."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class ActivePrompt:
    name: str
    version: str
    content: str


async def get_active_prompt(session: AsyncSession, prompt_name: str) -> ActivePrompt:
    row = (await session.execute(
        text(
            """
            SELECT prompt_name, version, content
            FROM prompt_versions
            WHERE prompt_name = :name AND is_active = TRUE
            ORDER BY activated_at DESC NULLS LAST
            LIMIT 1
            """
        ),
        {"name": prompt_name},
    )).mappings().first()
    if not row:
        raise RuntimeError(
            f"No active prompt found for '{prompt_name}'. "
            "Run scripts/seed_initial_data.py to load defaults."
        )
    return ActivePrompt(
        name=row["prompt_name"], version=row["version"], content=row["content"]
    )
