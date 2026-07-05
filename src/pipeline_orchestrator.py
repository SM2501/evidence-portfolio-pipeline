"""
pipeline_orchestrator.py — Main entry point.

Flow:
  1. Load config, initialize LLM client (and Neo4j if --load-graph)
  2. Ingest Media Cloud articles (--articles) and/or PDFs (--pdf-dir)
  3. Process in batches: Classifier -> Extractor
  4. Validate against the framework schema; enforce trust-layer invariants
  5. Append records to outputs/extracted.jsonl (append-only, resumable)
  6. Optionally load into Neo4j; print summary statistics

Resumability: outputs/processed_ledger.json records every document id with a
status. Re-running skips anything already 'ok', so you can do 100 tonight and
the rest next week (--limit N). Use --retry-failed to reattempt failures.

Examples:
  python src/pipeline_orchestrator.py --articles data/articles.csv --limit 100
  python src/pipeline_orchestrator.py --pdf-dir data/pdfs --limit 25
  python src/pipeline_orchestrator.py --articles data/articles.csv --load-graph
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent))

from article_ingestor import ingest_articles
from classifier import classify_article
from extractor import extract_evidence
from llm_client import LLMClient, load_config, setup_logging
from pdf_parser import parse_pdf
from validation import (
    enforce_extraction_invariants,
    validate_classification,
    validate_extraction,
)

logger = logging.getLogger("orchestrator")


# ----------------------------- ledger ------------------------------------
def load_ledger(path: Path) -> dict:
    if path.exists():
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {"processed": {}}


def save_ledger(ledger: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, indent=2)
    tmp.replace(path)


def mark(ledger: dict, doc_id: str, status: str, detail: str = "") -> None:
    ledger["processed"][doc_id] = {
        "status": status,
        "detail": detail[:300],
        "ts": datetime.now(timezone.utc).isoformat(),
    }


# ----------------------------- input loading -----------------------------
def load_documents(args, config: dict) -> list[dict]:
    """Return normalized docs: {id, title, outlet, date, url, text, kind}."""
    docs: list[dict] = []
    if args.articles:
        for rec in ingest_articles(args.articles, config["paths"]["intermediate_articles"]):
            rec["kind"] = "article"
            docs.append(rec)
    if args.pdf_dir:
        pdf_paths = sorted(Path(args.pdf_dir).glob("*.pdf"))
        logger.info("Found %d PDFs in %s", len(pdf_paths), args.pdf_dir)
        for path in pdf_paths:
            try:
                parsed = parse_pdf(path)
            except Exception as exc:  # noqa: BLE001 — log and skip, never crash the run
                logger.error("PDF skipped (%s): %s", path.name, exc)
                continue
            docs.append({
                "id": f"pdf-{path.stem}",
                "title": parsed["title"],
                "outlet": parsed["authors"] or "document",
                "date": parsed["date"] or "",
                "url": "",
                "text": parsed["full_text"],
                "kind": "pdf",
            })
    return docs


# ----------------------------- main --------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Evidence Portfolio Pipeline")
    parser.add_argument("--articles", help="Media Cloud CSV/JSON export path")
    parser.add_argument("--pdf-dir", help="Folder of research-paper/report PDFs")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max NEW documents to process this run (batch-at-a-time workflow)")
    parser.add_argument("--load-graph", action="store_true",
                        help="Load this run's records into Neo4j afterwards")
    parser.add_argument("--retry-failed", action="store_true",
                        help="Reattempt documents previously marked failed")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    if not args.articles and not args.pdf_dir:
        parser.error("Provide --articles and/or --pdf-dir")

    config = load_config(args.config)
    setup_logging(config)

    paths = config["paths"]
    ledger_path = Path(paths["ledger"])
    extracted_path = Path(paths["extracted_records"])
    extracted_path.parent.mkdir(parents=True, exist_ok=True)

    ledger = load_ledger(ledger_path)
    llm = LLMClient(config)

    docs = load_documents(args, config)
    skip_statuses = {"ok"} if args.retry_failed else {"ok", "failed"}
    pending = [d for d in docs
               if ledger["processed"].get(d["id"], {}).get("status") not in skip_statuses]
    if args.limit:
        pending = pending[: args.limit]

    logger.info("Documents: %d total, %d already in ledger, %d in this run",
                len(docs), len(docs) - len(pending), len(pending))
    if not pending:
        print("Nothing to do — all requested documents already processed. "
              "Use --retry-failed to reattempt failures.")
        return

    batch_size = int(config["batching"]["articles_per_batch"])
    ctx = config["context_window"]
    stats = {"ok": 0, "failed": 0, "validation_warnings": 0, "tags": {}}
    run_records: list[dict] = []

    with open(extracted_path, "a", encoding="utf-8") as out_fh:
        for start in range(0, len(pending), batch_size):
            batch = pending[start:start + batch_size]
            for doc in tqdm(batch, desc=f"batch {start // batch_size + 1}", unit="doc"):
                doc_id = doc["id"]
                try:
                    classification = classify_article(
                        llm, doc["text"], doc,
                        max_input_tokens=int(ctx["max_tokens_per_chunk"]),
                    )
                    ok_c, errs_c = validate_classification(classification, doc_id)
                    if not ok_c:
                        stats["validation_warnings"] += len(errs_c)

                    record = extract_evidence(
                        llm, doc["text"], doc,
                        tags=classification.get("tags", []),
                        max_input_tokens=int(ctx["max_tokens_per_chunk"]),
                        overlap=int(ctx["chunk_overlap_tokens"]),
                    )
                    record["classification"] = classification
                    record = enforce_extraction_invariants(record)
                    ok_e, errs_e = validate_extraction(record, doc_id)
                    if not ok_e:
                        stats["validation_warnings"] += len(errs_e)
                        record["validation_errors"] = errs_e

                    out_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                    out_fh.flush()
                    run_records.append(record)
                    mark(ledger, doc_id, "ok")
                    stats["ok"] += 1
                    for tag in record.get("tags", []):
                        stats["tags"][tag] = stats["tags"].get(tag, 0) + 1

                except KeyboardInterrupt:
                    logger.warning("Interrupted — ledger saved; re-run to resume.")
                    save_ledger(ledger, ledger_path)
                    raise
                except Exception as exc:  # noqa: BLE001 — log, mark, continue
                    logger.error("Document %s failed: %s", doc_id, exc)
                    mark(ledger, doc_id, "failed", str(exc))
                    stats["failed"] += 1
            save_ledger(ledger, ledger_path)  # checkpoint every batch

    if args.load_graph and run_records:
        from neo4j_client import Neo4jClient
        with Neo4jClient(config) as neo:
            neo.create_constraints()
            counts = neo.build_graph(run_records)
        logger.info("Neo4j load counts: %s", counts)

    print("\n===== RUN SUMMARY =====")
    print(f"  processed ok:        {stats['ok']}")
    print(f"  failed (in ledger):  {stats['failed']}")
    print(f"  validation warnings: {stats['validation_warnings']}")
    print("  tag distribution:")
    for tag, n in sorted(stats["tags"].items(), key=lambda kv: -kv[1]):
        print(f"    {tag:<22} {n}")
    print(f"  records file: {extracted_path}")
    print(f"  ledger:       {ledger_path}")
    print("\nNext: python src/verify.py --input", extracted_path)


if __name__ == "__main__":
    main()
