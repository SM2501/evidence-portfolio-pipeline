"""
article_ingestor.py — Load Media Cloud exports into one normalized schema.

Output schema per article:
  {id, title, outlet, date, url, text}

Media Cloud column names vary by export era; COLUMN_ALIASES covers the
common ones. Unknown layouts fail loudly with the observed columns listed.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

COLUMN_ALIASES: dict[str, list[str]] = {
    "id":     ["id", "stories_id", "story_id", "article_id", "guid"],
    "title":  ["title", "story_title", "headline"],
    "outlet": ["outlet", "media_name", "media", "source", "publication", "domain"],
    "date":   ["date", "publish_date", "publish_day", "published_at", "collect_date"],
    "url":    ["url", "story_url", "link", "guid_url"],
    "text":   ["text", "story_text", "full_text", "content", "body", "article_text"],
}

MIN_TEXT_CHARS = 200  # anything shorter is a stub/paywall fragment


def _map_columns(columns: list[str]) -> dict[str, str]:
    lower = {c.lower().strip(): c for c in columns}
    mapping: dict[str, str] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lower:
                mapping[canonical] = lower[alias]
                break
    missing = {"title", "text"} - mapping.keys()
    if missing:
        raise ValueError(
            f"Could not find required column(s) {sorted(missing)} in export. "
            f"Observed columns: {columns}. Add your column name to COLUMN_ALIASES."
        )
    return mapping


def _load_dataframe(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, dtype=str, on_bad_lines="warn", low_memory=False)
    if path.suffix.lower() in {".json", ".jsonl"}:
        try:
            return pd.read_json(path, dtype=str, lines=path.suffix.lower() == ".jsonl")
        except ValueError:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            rows = payload.get("stories", payload) if isinstance(payload, dict) else payload
            return pd.DataFrame(rows).astype(str)
    raise ValueError(f"Unsupported input format: {path.suffix} (expected .csv/.json/.jsonl)")


def ingest_articles(input_path: str | Path, output_path: str | Path) -> list[dict]:
    """
    Normalize a Media Cloud export, drop empty-text rows, dedupe by id/url,
    and persist the intermediate JSON. Returns the normalized records.
    """
    input_path, output_path = Path(input_path), Path(output_path)
    df = _load_dataframe(input_path)
    logger.info("Loaded %d rows from %s", len(df), input_path.name)

    mapping = _map_columns(list(df.columns))
    records: list[dict] = []
    dropped_empty = 0

    for i, row in df.iterrows():
        text = str(row.get(mapping.get("text", ""), "") or "").strip()
        if text.lower() in {"nan", "none"}:
            text = ""
        if len(text) < MIN_TEXT_CHARS:
            dropped_empty += 1
            continue

        def _field(name: str, default: str = "") -> str:
            col = mapping.get(name)
            value = str(row.get(col, default) or default).strip() if col else default
            return "" if value.lower() in {"nan", "none"} else value

        article_id = _field("id") or f"row-{i}"
        records.append({
            "id": article_id,
            "title": _field("title", "(untitled)"),
            "outlet": _field("outlet", "unknown"),
            "date": _field("date")[:10],
            "url": _field("url"),
            "text": text,
        })

    # Dedupe (Media Cloud exports frequently contain repeats)
    seen: set[str] = set()
    unique: list[dict] = []
    for rec in records:
        key = rec["url"] or rec["id"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(rec)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(unique, fh, ensure_ascii=False)

    logger.info(
        "Normalized %d articles (%d dropped for empty/short text, %d duplicates removed) -> %s",
        len(unique), dropped_empty, len(records) - len(unique), output_path,
    )
    return unique
