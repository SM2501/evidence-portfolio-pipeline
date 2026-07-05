"""
verify.py — Trust layer: the ONLY code path that flips human_verified to True.

For each unverified record: display a summary, prompt y/n/skip.
  y    -> requires a typed verification note (your testimony), sets
          human_verified=True, appends an audit event
  n    -> records an explicit rejection note; record stays unverified
  skip -> no state change
  q    -> save and quit

Every decision is appended to an audit log (JSONL, hash-chained). The final
report generator consumes ONLY the verified export this tool writes.

Usage:
  python src/verify.py --input outputs/extracted.jsonl
  python src/verify.py --input outputs/extracted.jsonl --output outputs/verified.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------- audit log -----------------------------------
class AuditLog:
    """Append-only, hash-chained JSONL. Each event embeds the previous hash."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _last_hash(self) -> str:
        if not self.path.exists():
            return "GENESIS"
        last = "GENESIS"
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    last = json.loads(line)["hash"]
        return last

    def append(self, event: dict) -> None:
        event = dict(event)
        event["ts"] = _now_iso()
        event["prev_hash"] = self._last_hash()
        payload = json.dumps(event, sort_keys=True, ensure_ascii=False)
        event["hash"] = hashlib.sha256(payload.encode()).hexdigest()
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    def verify_chain(self) -> bool:
        if not self.path.exists():
            return True
        prev = "GENESIS"
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                event = json.loads(line)
                claimed = event.pop("hash")
                if event.get("prev_hash") != prev:
                    return False
                payload = json.dumps(event, sort_keys=True, ensure_ascii=False)
                if hashlib.sha256(payload.encode()).hexdigest() != claimed:
                    return False
                prev = claimed
        return True


# --------------------------- record io -----------------------------------
def load_records(path: Path) -> list[dict]:
    records: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        if path.suffix == ".jsonl":
            for line in fh:
                if line.strip():
                    records.append(json.loads(line))
        else:
            payload = json.load(fh)
            records = payload if isinstance(payload, list) else payload.get("records", [])
    return records


def save_records(records: list[dict], path: Path) -> None:
    if path.suffix == ".jsonl":
        with open(path, "w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    else:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(records, fh, indent=2, ensure_ascii=False)


# --------------------------- display -------------------------------------
def show_record(index: int, total: int, rec: dict) -> None:
    meta = rec.get("metadata", {})
    print("\n" + "=" * 72)
    print(f"[{index + 1}/{total}] {meta.get('title', '(untitled)')}")
    print(f"  outlet: {meta.get('outlet','')}   date: {meta.get('date','')}")
    print(f"  url:    {meta.get('url','') or '—'}")
    print(f"  tags:   {', '.join(rec.get('tags', []))}")
    print(f"\n  summary: {rec.get('core_summary','')}")
    dps = rec.get("key_data_points", []) or []
    if dps:
        print("  data points:")
        for dp in dps[:5]:
            print(f"    - {dp.get('datum','')}  [{dp.get('source_in_article','')}]")
    quotes = rec.get("direct_quotes", []) or []
    if quotes:
        print("  quotes:")
        for q in quotes[:3]:
            print(f'    - "{q.get("quote","")[:140]}" — {q.get("speaker","?")}')
    errs = rec.get("validation_errors")
    if errs:
        print(f"  !! validation warnings: {errs}")


# --------------------------- main ----------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Human verification CLI (trust layer)")
    parser.add_argument("--input", required=True, help="extracted.jsonl (or .json)")
    parser.add_argument("--output", default="outputs/verified.json",
                        help="Verified-only export for report generation")
    parser.add_argument("--audit", default="outputs/audit_log.jsonl")
    parser.add_argument("--check-audit", action="store_true",
                        help="Only verify the audit-log hash chain, then exit")
    args = parser.parse_args()

    audit = AuditLog(args.audit)
    if args.check_audit:
        ok = audit.verify_chain()
        print("Audit log hash chain: " + ("intact." if ok else "BROKEN — history was altered."))
        sys.exit(0 if ok else 2)

    input_path = Path(args.input)
    records = load_records(input_path)
    pending_idx = [i for i, r in enumerate(records)
                   if not r.get("extraction_provenance", {}).get("human_verified")]
    print(f"Loaded {len(records)} record(s); {len(pending_idx)} awaiting verification.")
    print("Commands: y = verify (typed note required), n = reject, skip, q = save & quit.")
    print("Open the article URL yourself before pressing y — the note is your testimony.\n")

    changed = 0
    for count, i in enumerate(pending_idx):
        rec = records[i]
        show_record(count, len(pending_idx), rec)
        answer = input("\nverify? [y/n/skip/q] ").strip().lower()

        if answer == "q":
            break
        if answer == "y":
            note = ""
            while not note:
                note = input("verification_note (what did you open, what did you check?)> ").strip()
                if not note:
                    print("  A verification with no typed note is not a verification.")
            prov = rec.setdefault("extraction_provenance", {})
            prov["human_verified"] = True
            prov["verification_note"] = note
            prov["verified_at"] = _now_iso()
            audit.append({
                "event": "verify",
                "article_id": rec.get("metadata", {}).get("article_id"),
                "note": note,
            })
            changed += 1
        elif answer == "n":
            reason = input("rejection_note (why is this record wrong/unusable?)> ").strip()
            rec.setdefault("extraction_provenance", {})["rejection_note"] = reason or "rejected"
            audit.append({
                "event": "reject",
                "article_id": rec.get("metadata", {}).get("article_id"),
                "note": reason,
            })
        # skip: no state change, no audit noise

    save_records(records, input_path)
    verified = [r for r in records
                if r.get("extraction_provenance", {}).get("human_verified") is True]
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    save_records(verified, output_path)

    print(f"\nSession done. Newly verified: {changed}. Total verified: {len(verified)}.")
    print(f"  updated:  {input_path}")
    print(f"  verified: {output_path}")
    print(f"  audit:    {args.audit}")
    print(f"\nNext: python src/report_generator.py --verified {output_path}")


if __name__ == "__main__":
    main()
