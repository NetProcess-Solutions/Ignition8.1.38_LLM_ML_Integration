"""
Symphony video clip capture (design section 7.3).

Two paths, same writer:

  - event_triggered: called from the orchestrator when a defect_event,
    downtime_event, or alarm is recorded. Asks the Symphony video
    server for a clip that brackets the event time and stores the
    handle in `event_clips`.
  - backfill: nightly sweep that finds events newer than `since` that
    have no clip rows yet and tries to fetch them.

The actual Symphony API is plant-specific and is intentionally hidden
behind `_request_clip()`, which returns a placeholder dict in MVP. Wire
the real HTTP/RTSP/Bosch SDK call in there when the camera credentials
are available.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings


CAMERA_ID_BY_EQUIPMENT: dict[str, list[str]] = {
    # equipment_id -> [camera_id, ...] mapping. Update as new cameras
    # are commissioned. Multi-camera equipment yields multiple clips.
    "coater1.applicator":   ["cam-c1-app-01"],
    "coater1.zone3":        ["cam-c1-z3-01", "cam-c1-z3-02"],
    "coater1.tenter":       ["cam-c1-tnt-01"],
    "coater1.line_general": ["cam-c1-overview"],
}

CLIP_PRE_SECONDS  = 30
CLIP_POST_SECONDS = 60


@dataclass
class CaptureRequest:
    event_id: uuid.UUID
    event_table: str  # 'defect_events' | 'downtime_events' | 'alarms'
    event_time: datetime
    equipment_id: str | None
    line_id: str


def _cameras_for(equipment_id: str | None) -> list[str]:
    if equipment_id and equipment_id in CAMERA_ID_BY_EQUIPMENT:
        return list(CAMERA_ID_BY_EQUIPMENT[equipment_id])
    return list(CAMERA_ID_BY_EQUIPMENT.get("coater1.line_general", []))


def _request_clip(camera_id: str, start: datetime, end: datetime) -> dict[str, Any]:
    """
    Plant-specific Symphony adapter. Stub returns a synthetic handle so
    the upstream pipeline is end-to-end testable. Replace with the real
    HTTP call (see ops runbook for API key location).
    """
    handle = f"symphony://{camera_id}/{int(start.timestamp())}-{int(end.timestamp())}"
    settings = get_settings()
    return {
        "storage_handle": handle,
        "extraction_status": "stub",
        "camera_location": getattr(settings, f"camera_loc_{camera_id}", camera_id),
    }


async def _insert_clip(
    session: AsyncSession,
    req: CaptureRequest,
    camera_id: str,
    clip: dict[str, Any],
    start: datetime,
    end: datetime,
) -> uuid.UUID:
    clip_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO event_clips (
                id, event_id, event_table, line_id, camera_id,
                camera_location, clip_start, clip_end,
                storage_handle, extraction_status
            ) VALUES (
                :id, :eid, :et, :line, :cam, :loc,
                :start, :end, :handle, :status
            )
            ON CONFLICT (event_id, camera_id, clip_start) DO NOTHING
            """
        ),
        {
            "id": clip_id, "eid": req.event_id, "et": req.event_table,
            "line": req.line_id, "cam": camera_id,
            "loc": clip.get("camera_location"),
            "start": start, "end": end,
            "handle": clip.get("storage_handle"),
            "status": clip.get("extraction_status", "requested"),
        },
    )
    return clip_id


async def capture_for_event(
    session: AsyncSession, req: CaptureRequest,
) -> list[uuid.UUID]:
    """
    Synchronous (event-triggered) capture. Returns the list of created
    clip ids. Caller is responsible for commit; this method only flushes.
    """
    start = req.event_time - timedelta(seconds=CLIP_PRE_SECONDS)
    end = req.event_time + timedelta(seconds=CLIP_POST_SECONDS)
    created: list[uuid.UUID] = []
    for cam in _cameras_for(req.equipment_id):
        clip = _request_clip(cam, start, end)
        created.append(await _insert_clip(session, req, cam, clip, start, end))
    return created


async def backfill_since(
    session: AsyncSession, since: datetime,
) -> dict[str, int]:
    """
    Find defect/downtime events without clips and request them. Useful
    after a Symphony outage.
    """
    rows = (await session.execute(
        text(
            """
            SELECT e.id, 'defect_events' AS et, e.detected_time AS t,
                   e.equipment_id, e.line_id
            FROM defect_events e
            LEFT JOIN event_clips c
              ON c.event_id = e.id AND c.event_table = 'defect_events'
            WHERE e.detected_time >= :since AND c.id IS NULL
            UNION ALL
            SELECT d.id, 'downtime_events' AS et, d.start_time AS t,
                   d.equipment_id, d.line_id
            FROM downtime_events d
            LEFT JOIN event_clips c
              ON c.event_id = d.id AND c.event_table = 'downtime_events'
            WHERE d.start_time >= :since AND c.id IS NULL
            """
        ),
        {"since": since},
    )).all()
    requested = 0
    clips = 0
    for row in rows:
        req = CaptureRequest(
            event_id=row[0], event_table=row[1], event_time=row[2],
            equipment_id=row[3], line_id=row[4],
        )
        new_clips = await capture_for_event(session, req)
        requested += 1
        clips += len(new_clips)
    await session.commit()
    return {"events": requested, "clips": clips}
