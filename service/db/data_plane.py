"""
Data-plane adapter seam.

The agentic harness should not know which platform stores the relational
tables, where the vector index lives, or how the audit log is made
immutable. It should know **what operations it needs**.

This module defines a typed `DataPlane` Protocol that captures the
operations the harness performs against the data plane today, plus
factory functions that return a concrete implementation chosen by
configuration (`settings.data_plane_backend`).

Backends:
  * `postgres` (default) — wraps `service/db/connection.py`. This is the
    MVP implementation; identical behavior to today's direct calls.
  * `databricks` — placeholder. Implementation lands once IT confirms
    Databricks is the target and we have workspace + token credentials.

The point of this module is **not** to abstract SQL prematurely. The
point is to make the boundary explicit so the IT meeting can answer
"where does this plane live?" without us having to point at hundreds of
call sites scattered across services.

See `docs/THREE_PLANE_ARCHITECTURE.md` §4.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession


@runtime_checkable
class DataPlane(Protocol):
    """
    Operations the agentic harness performs against the data plane.

    Implementations are free to map each method to whatever storage
    primitives their platform provides — a single Postgres instance, a
    Databricks workspace + Vector Search, an RDBMS + AI Search pair, etc.

    Today this Protocol is intentionally narrow: it exposes a session
    context manager so the harness can keep using SQLAlchemy against
    Postgres without rewrites, while also declaring the higher-level
    capabilities every backend must provide. As we migrate call sites
    off raw SQL, methods will be added here and removed from the
    direct-DB path.
    """

    backend_name: str

    @asynccontextmanager
    def session(self) -> AsyncIterator[AsyncSession]:  # type: ignore[empty-body]
        """
        Yield a transactional session for relational reads/writes.

        The Postgres backend yields a real `AsyncSession`. Non-SQL
        backends (Databricks) will yield a thin shim that exposes only
        the subset of SQLAlchemy methods the harness actually uses.
        """
        ...

    async def healthcheck(self) -> dict[str, Any]:
        """Return backend-specific health info for `/api/health`."""
        ...

    async def dispose(self) -> None:
        """Release pooled resources on shutdown."""
        ...


# ---------------------------------------------------------------------------
# Postgres implementation — wraps the existing connection module.
# ---------------------------------------------------------------------------

class PostgresDataPlane:
    """MVP backend. Behavior matches today's direct DB usage exactly."""

    backend_name = "postgres"

    def __init__(self) -> None:
        # Imported lazily so importing this module does not require the
        # DB to be reachable (e.g. for `--list` on the MCP server).
        from db.connection import SessionFactory, dispose_engine, engine

        self._SessionFactory = SessionFactory
        self._dispose_engine = dispose_engine
        self._engine = engine

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._SessionFactory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    async def healthcheck(self) -> dict[str, Any]:
        from sqlalchemy import text

        async with self._SessionFactory() as s:
            row = (await s.execute(text("SELECT 1 AS ok"))).scalar_one()
        return {"backend": self.backend_name, "ok": row == 1}

    async def dispose(self) -> None:
        await self._dispose_engine()


# ---------------------------------------------------------------------------
# Databricks placeholder — concrete implementation deferred.
# ---------------------------------------------------------------------------

class DatabricksDataPlane:
    """
    Placeholder Databricks backend.

    Concrete implementation lands once IT confirms:
      * Workspace URL + auth (PAT, OAuth M2M, or service principal).
      * Whether vector search is Databricks Vector Search or a separate
        managed service (Azure AI Search / Snowflake Cortex / etc.).
      * Whether OLTP-style writes (messages, audit_log) live in Delta or
        an adjacent RDBMS.

    Until then this class exists so the configuration switch is real and
    the architecture doc can point at it.
    """

    backend_name = "databricks"

    def __init__(self) -> None:
        raise NotImplementedError(
            "DatabricksDataPlane is a placeholder. "
            "Implementation depends on IT decisions on workspace, vector "
            "store, and OLTP target. See docs/THREE_PLANE_ARCHITECTURE.md "
            "and the IT-meeting capability map."
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:  # pragma: no cover
        raise NotImplementedError
        yield  # type: ignore[unreachable]

    async def healthcheck(self) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    async def dispose(self) -> None:  # pragma: no cover
        return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_BACKENDS: dict[str, type] = {
    "postgres":   PostgresDataPlane,
    "databricks": DatabricksDataPlane,
}

_singleton: DataPlane | None = None


def get_data_plane() -> DataPlane:
    """
    Return the configured data-plane singleton.

    Backend is chosen by `settings.data_plane_backend` (default
    `postgres`). The harness should depend on this Protocol, not on
    `db.connection` directly.
    """
    global _singleton
    if _singleton is not None:
        return _singleton

    # Lazy import so this module is importable in environments without
    # pydantic-settings configured (e.g. the MCP `--list` introspection).
    from config.settings import get_settings

    name = getattr(get_settings(), "data_plane_backend", "postgres")
    cls = _BACKENDS.get(name)
    if cls is None:
        raise ValueError(
            f"unknown data_plane_backend: {name!r} "
            f"(known: {sorted(_BACKENDS)})"
        )
    _singleton = cls()
    return _singleton


async def dispose_data_plane() -> None:
    global _singleton
    if _singleton is not None:
        await _singleton.dispose()
        _singleton = None
