"""
B5 — extract supervised fine-tuning pairs from production logs.

PLACEHOLDER. See ml/finetune/README.md for the full pipeline.

Run as:
    python -m ml.finetune.extract_pairs --since 2025-10-01 --out pairs.jsonl
"""
from __future__ import annotations

import argparse


def main() -> None:
    p = argparse.ArgumentParser(description="Extract SFT pairs from messages")
    p.add_argument("--since", required=True, help="ISO date, e.g. 2025-10-01")
    p.add_argument("--until", default=None, help="ISO date; default = now")
    p.add_argument("--out", required=True, help="Output JSONL path")
    p.add_argument("--alignment", default="confirmed",
                   help="outcome_linkage alignment to mine; "
                        "use 'confirmed,likely' for a wider net")
    p.add_argument("--min-cite-count", type=int, default=1,
                   help="Drop answers that cited fewer than N sources")
    args = p.parse_args()

    # TODO(B5): implement.
    #   1. Open async DB session.
    #   2. SELECT m.context_snapshot, m.content, m.prompt_version, ...
    #      FROM messages m JOIN outcome_linkages ol ON ol.message_id=m.id
    #      WHERE ol.alignment = ANY(:alignments) AND m.created_at >= :since ...
    #   3. For each row:
    #        - reconstruct (system_prompt, user_block, assistant_response)
    #        - call redact.scrub() to strip PII
    #        - emit one JSONL line in the target chat template
    raise NotImplementedError("B5 SFT extraction not implemented")


if __name__ == "__main__":
    main()
