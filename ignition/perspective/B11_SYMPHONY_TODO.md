# B11 — Symphony video clip ingestion (PARTIAL — NEEDS PLANT-SPECIFIC API CALL)

The Python-side scaffolding exists in
[service/services/symphony_capture.py](service/services/symphony_capture.py)
including:

- The `event_clips` table writer
- `event_triggered()` and `backfill()` entry points
- The camera-by-equipment mapping (`CAMERA_ID_BY_EQUIPMENT`)
- The pre/post-event window constants

What's STILL a stub: `_request_clip()` returns a placeholder. To wire
the real Symphony video server, replace its body with whichever of
these your install uses:

## Option A — Bosch / Avigilon REST API
```python
import httpx, base64
async def _request_clip(camera_id, start_ts, end_ts):
    auth = base64.b64encode(f"{user}:{pw}".encode()).decode()
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            f"{SYMPHONY_BASE}/v1/cameras/{camera_id}/clips",
            json={"start": start_ts.isoformat(), "end": end_ts.isoformat(),
                  "format": "mp4"},
            headers={"Authorization": f"Basic {auth}"},
        )
        r.raise_for_status()
        return {"storage_handle": r.json()["clip_url"],
                "camera_id": camera_id,
                "start_time": start_ts, "end_time": end_ts}
```

## Option B — RTSP pull + ffmpeg
For installs without a clip API. ffmpeg invocation:
```
ffmpeg -ss <start_ts> -i rtsp://<server>/<camera_id> \
       -t <duration_s> -c copy /var/clips/<event_id>.mp4
```
Run via `asyncio.create_subprocess_exec`, then upload the resulting
file to the storage backend you've chosen (S3, MinIO, NAS) and return
the resulting URI as `storage_handle`.

## Option C — File system scrape
If clips are continuously written by the camera server, just compute
the expected file path + offset and return that as the handle. No
async work needed.

---

Required pre-flight on the gateway:

1. The CAMERA_ID_BY_EQUIPMENT map at the top of `symphony_capture.py`
   must reflect actual camera IDs in your Symphony install.
2. Settings additions (already in `service/config/settings.py` if you
   added them; if not, add):
   - `symphony_base_url`
   - `symphony_user` / `symphony_password` (or service account token)
   - `symphony_clip_pre_seconds`, `symphony_clip_post_seconds`
3. A periodic job to call `backfill()` nightly. Recommend: add to the
   APScheduler block we'll wire next to `outcome_followups`.
