"""One-shot migration: parse public.artworks COPY block from the Supabase
cluster dump and load rows into the ruggles_artworks_prod DynamoDB table.

Usage:
    python migrate_to_dynamodb.py --dry-run     # parse + print, no writes
    python migrate_to_dynamodb.py --load        # parse + write to DynamoDB
"""
import argparse
import os
import sys
from pathlib import Path

BACKUP_PATH = Path(__file__).parent / "db_cluster-17-07-2025@20-26-32.backup"
TABLE_NAME = "ruggles_artworks_prod"
REGION = "us-east-1"

COLUMNS = [
    "id", "title", "description", "image_url", "image_public_id",
    "artist_name", "created_at", "question", "gpt_response", "tags",
    "proportion_score", "proportion_rationale", "proportion_tips",
    "line_quality_score", "line_quality_rationale", "line_quality_tips",
    "evaluation_version",
    "value_light_score", "value_light_rationale", "value_light_tips",
    "detail_texture_score", "detail_texture_rationale", "detail_texture_tips",
    "composition_perspective_score", "composition_perspective_rationale",
    "composition_perspective_tips",
    "form_volume_score", "form_volume_rationale", "form_volume_tips",
    "mood_expression_score", "mood_expression_rationale", "mood_expression_tips",
    "overall_realism_score", "overall_realism_rationale", "overall_realism_tips",
    "artwork_date", "sketch_type",
]
INT_COLUMNS = {c for c in COLUMNS if c.endswith("_score")}
ARRAY_COLUMNS = {c for c in COLUMNS if c.endswith("_tips")} | {"tags"}


def unescape_pg_copy(field: str) -> str:
    """Unescape a PG COPY TEXT field (handles \\t \\n \\r \\\\ etc)."""
    out, i, n = [], 0, len(field)
    while i < n:
        c = field[i]
        if c == "\\" and i + 1 < n:
            nxt = field[i + 1]
            mapping = {"t": "\t", "n": "\n", "r": "\r", "b": "\b",
                       "f": "\f", "v": "\v", "\\": "\\"}
            if nxt in mapping:
                out.append(mapping[nxt])
                i += 2
                continue
        out.append(c)
        i += 1
    return "".join(out)


def parse_pg_array(raw: str) -> list[str]:
    """Parse a PG text[] literal like {"a","b,c","d\\"e"} into a Python list."""
    if raw == "{}" or not raw:
        return []
    if not (raw.startswith("{") and raw.endswith("}")):
        raise ValueError(f"Malformed PG array: {raw[:60]!r}")
    body = raw[1:-1]
    items, buf, i, n = [], [], 0, len(body)
    in_quotes = False
    while i < n:
        c = body[i]
        if in_quotes:
            if c == "\\" and i + 1 < n:
                buf.append(body[i + 1])
                i += 2
                continue
            if c == '"':
                in_quotes = False
                i += 1
                continue
            buf.append(c)
            i += 1
        else:
            if c == '"':
                in_quotes = True
                i += 1
                continue
            if c == ",":
                items.append("".join(buf))
                buf = []
                i += 1
                continue
            buf.append(c)
            i += 1
    if buf or body.endswith(","):
        items.append("".join(buf))
    return items


def extract_copy_block(backup_text: str) -> list[str]:
    """Return the list of raw TSV data lines between the COPY header and \\."""
    lines = backup_text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if line.startswith("COPY public.artworks ") and line.rstrip().endswith("FROM stdin;"):
            start = idx + 1
            break
    if start is None:
        raise RuntimeError("Could not find COPY public.artworks header")
    end = None
    for idx in range(start, len(lines)):
        if lines[idx] == "\\.":
            end = idx
            break
    if end is None:
        raise RuntimeError("Could not find end-of-COPY marker")
    return lines[start:end]


def parse_row(line: str) -> dict:
    fields = line.split("\t")
    if len(fields) != len(COLUMNS):
        raise ValueError(
            f"Expected {len(COLUMNS)} columns, got {len(fields)}: "
            f"{line[:120]!r}"
        )
    item = {"entity_type": "artwork"}
    for col, raw in zip(COLUMNS, fields):
        if raw == "\\N":
            continue  # NULL -> omit attribute
        val = unescape_pg_copy(raw)
        if col in INT_COLUMNS:
            item[col] = int(val)
        elif col in ARRAY_COLUMNS:
            item[col] = parse_pg_array(val)
        else:
            item[col] = val
    return item


def main() -> int:
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true",
                       help="parse + print summary, no writes")
    group.add_argument("--load", action="store_true",
                       help="parse + write to DynamoDB")
    args = ap.parse_args()

    backup_text = BACKUP_PATH.read_text(encoding="utf-8")
    raw_lines = extract_copy_block(backup_text)
    items = [parse_row(line) for line in raw_lines]
    print(f"Parsed {len(items)} rows.")

    sample = items[0]
    print("\nSample item (first row):")
    for k, v in sample.items():
        preview = v if not isinstance(v, str) else (v[:80] + "…" if len(v) > 80 else v)
        print(f"  {k}: {preview!r}")

    sizes = [len(str(it)) for it in items]
    print(f"\nApprox item sizes: min={min(sizes)}, max={max(sizes)}, "
          f"avg={sum(sizes)//len(sizes)} chars")

    if args.dry_run:
        print("\nDry run complete. No writes performed.")
        return 0

    import boto3
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    table = dynamodb.Table(TABLE_NAME)
    written = 0
    with table.batch_writer() as batch:
        for it in items:
            batch.put_item(Item=it)
            written += 1
    print(f"\nWrote {written} items to {TABLE_NAME}.")

    resp = table.scan(Select="COUNT")
    print(f"Table scan count after load: {resp['Count']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
