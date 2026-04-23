# Ingestion Guide

The current MVP ingester (`service/scripts/ingest.py`) understands:

- **Plain text / markdown files** in a directory (one document per file)
- **Quality CSV** files with columns including `test_time`, `test_type`,
  `result`, `sample_id`, `notes`

For your real plant report formats (PDF, Word, Excel, email exports, etc.)
you have two options. Either is valid; pick based on volume and friction.

## Option A: convert upstream → text/CSV → existing ingester

Best when reports are produced by an existing system that you can script
against. Examples:

- **PDF reports**: use `pdftotext` (poppler-utils) on the source folder to
  produce a parallel folder of `.txt` files, then point the ingester at it.
- **Word documents**: `pandoc -t plain in.docx -o out.txt`
- **Excel**: export the relevant sheet to CSV; if it's a quality test
  log, conform the column names to the quality CSV schema and use
  `--source-type quality_report_csv`.
- **Email**: extract the body to text files named by date.

The ingester is idempotent on `(source_type, source_id)`, so you can
re-run nightly without creating duplicates.

## Option B: write a parser adapter

Add a small Python parser in
`service/scripts/parsers/<your_format>.py` that returns a list of
`ParsedDocument` (and optionally structured event rows). Then add a new
branch in the `run()` function in `scripts/ingest.py` to dispatch to it.

Skeleton:

```python
def parse_my_format(path: Path, line_id: str) -> list[ParsedDocument]:
    docs = []
    # ... your parsing ...
    docs.append(ParsedDocument(
        source_type="maintenance_report",
        source_id=path.name,
        line_id=line_id,
        title="...",
        raw_text="...",
        document_date=datetime(...),
        author="...",
        shift="...",
        structured_fields={...},
        metadata={"source_path": str(path)},
    ))
    return docs
```

When you have an actual sample of each report format, share it and the
parser can be implemented against the real structure rather than guessed.

## Routing structured events to event tables

For data that is intrinsically event-like (downtime, quality results,
defects), you should populate the event tables (`downtime_events`,
`quality_results`, `defect_events`) **in addition to** the document
corpus. The quality CSV adapter does this already. For other formats, the
parser should emit both:

- A `ParsedDocument` per record (so the text becomes searchable)
- An event row inserted into the matching event table (so it appears in
  the structured "Recent Events" section of every chat response and
  becomes available for ML feature snapshots later)

## Re-embedding

If you change the embedding model, you must re-embed all chunks. There is
no automatic migration for this in MVP. Easiest path:

```sql
TRUNCATE document_chunks;
```

then re-run `ingest.py` for each source. Documents are preserved.

## Sample data

Drop sample text files into `ingestion/sample_data/` and the ingester can
be pointed there for an initial smoke test:

```
docker compose exec ai-service python -m scripts.ingest \
    --source-type maintenance_report --line-id coater1 \
    --path /app/../ingestion/sample_data/
```

(Adjust the in-container path to wherever you mount the directory.)
