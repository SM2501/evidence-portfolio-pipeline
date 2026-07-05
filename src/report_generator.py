"""
report_generator.py — Policy advocacy report from VERIFIED extractions only.

Framework invariant: only records with extraction_provenance.human_verified ==
True enter the report body. Unverified material is listed in Appendix C as
"reported but unverified" (counts + titles only, never treated as evidence).

Usage:
  python src/report_generator.py --verified outputs/verified.json
  python src/report_generator.py --verified outputs/verified.json \\
      --all-records outputs/extracted.jsonl --output outputs/policy_report.md
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


def _load(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as fh:
        if path.suffix == ".jsonl":
            return [json.loads(l) for l in fh if l.strip()]
        payload = json.load(fh)
        return payload if isinstance(payload, list) else payload.get("records", [])


def _cite(rec: dict) -> str:
    meta = rec.get("metadata", {})
    url = meta.get("url", "")
    core = f"{meta.get('outlet','?')}, {meta.get('date','?')}, \u201c{meta.get('title','?')}\u201d"
    return f"[{core}]({url})" if url else core


def _daterange(records: list[dict]) -> str:
    dates = sorted(r.get("metadata", {}).get("date", "") for r in records
                   if r.get("metadata", {}).get("date"))
    return f"{dates[0]} to {dates[-1]}" if dates else "n/a"


def generate_report(verified: list[dict], all_records: list[dict] | None) -> str:
    by_tag: dict[str, list[dict]] = defaultdict(list)
    for rec in verified:
        for tag in rec.get("tags", []):
            by_tag[tag].append(rec)

    states = Counter(r.get("classification", {}).get("geography", {}).get("state", "")
                     for r in verified)
    states.pop("", None)
    policies = Counter(p for r in verified
                       for p in r.get("policy_relevance", {}).get("policy_events_referenced", []))

    L: list[str] = []
    add = L.append

    add("# Policy Advocacy Evidence Report")
    add(f"\n*Generated {datetime.now(timezone.utc).date().isoformat()} — "
        f"built exclusively from human-verified extraction records.*\n")

    # ---- 1. Executive Summary -------------------------------------------
    add("## 1. Executive Summary\n")
    add(f"This report draws on **{len(verified)} human-verified article extractions** "
        f"spanning **{_daterange(verified)}** across **{len(states)} states**.\n")
    add("Top-line findings (each figure is a count of verified source articles):\n")
    headline_tags = ["TAPER_HARM", "VETERAN_SUICIDE", "PATIENT_ABANDONMENT",
                     "DISPLACEMENT", "DATA_LIMITATION"]
    for tag in headline_tags:
        n = len(by_tag.get(tag, []))
        if n:
            add(f"- {n} verified article(s) tagged **{tag}**.")
    add("\n**Methodology in one paragraph.** Articles were machine-classified against a "
        "fixed 12-tag framework and machine-extracted into structured evidence records; "
        "every record entered this report only after a human reviewer opened the source "
        "and logged a typed verification note into a hash-chained audit log.\n")
    add("**Limitations, stated up front.** Machine drafting can misread articles; "
        "verification confirms the article says what the record claims, not that the "
        "article itself is true. Media coverage is not incidence data. Counter-evidence "
        "coverage (GUIDELINE_DEFENSE) is reported in §4 alongside supporting material.\n")

    # ---- 2. Patient Impact Evidence -------------------------------------
    add("## 2. Patient Impact Evidence\n")
    impact = [r for r in verified if {"TAPER_HARM", "VETERAN_SUICIDE",
                                      "PATIENT_ABANDONMENT"} & set(r.get("tags", []))]
    if not impact:
        add("*No verified patient-impact records yet. Run verify.py on more extractions.*\n")
    for rec in impact[:6]:
        add(f"### {rec.get('metadata', {}).get('title', '(untitled)')}")
        add(f"- **Citation:** {_cite(rec)}")
        add(f"- **Summary:** {rec.get('core_summary','')}")
        for dp in (rec.get("key_data_points") or [])[:3]:
            add(f"- **Data point:** {dp.get('datum','')} "
                f"*(attributed to: {dp.get('source_in_article','?')})*")
        note = rec.get("extraction_provenance", {}).get("verification_note", "")
        add(f"- **Verification note:** {note}\n")
    if len(impact) > 6:
        add(f"*…and {len(impact) - 6} further verified impact records in Appendix B.*\n")

    add("### Aggregate: verified harms by state\n")
    add("| State | Verified articles |")
    add("|---|---|")
    for state, n in states.most_common(15):
        add(f"| {state} | {n} |")
    add("")

    # ---- 3. Policy Implementation Analysis ------------------------------
    add("## 3. Policy Implementation Analysis\n")
    if policies:
        add("| Policy event | Verified articles referencing it |")
        add("|---|---|")
        for pol, n in policies.most_common():
            add(f"| {pol} | {n} |")
        add("")
    what_worked = by_tag.get("KRATOM_POLICY", []) + by_tag.get("POLICY_TENSION", [])
    add("**What worked / what failed.** For each policy above, compare stated goal to the "
        "verified outcomes recorded in §2. Contrast is persuasion: reforms coverage credits "
        "with reducing harm belong here alongside failures.\n" if what_worked else
        "*Populate as more policy-tagged records are verified.*\n")

    # ---- 4. Evidence Controversies --------------------------------------
    add("## 4. Evidence Controversies\n")
    critiques = by_tag.get("GUIDELINE_CRITIQUE", []) + by_tag.get("DATA_LIMITATION", [])
    defenses = by_tag.get("GUIDELINE_DEFENSE", [])
    add(f"Verified critique-side records: {len(critiques)}. "
        f"Verified defense-side records: {len(defenses)}.\n")
    add("### Claims the coverage disputes\n")
    for rec in critiques[:5]:
        add(f"- {rec.get('core_summary','')} — {_cite(rec)}")
    add("\n### The strongest opposing case (verified)\n")
    if defenses:
        for rec in defenses[:5]:
            add(f"- {rec.get('core_summary','')} — {_cite(rec)}")
    else:
        add("- *No verified GUIDELINE_DEFENSE records yet — verify some before publishing; "
            "a portfolio that cannot state the opposing case accurately is weaker.*")
    add("\n### Claims our own records undermine\n")
    undermined = [(u, rec) for rec in verified
                  for u in rec.get("policy_relevance", {}).get("advocacy_claims_undermined", [])
                  if u and u != "none identified"]
    if undermined:
        for u, rec in undermined[:8]:
            add(f"- {u} — {_cite(rec)}")
    else:
        add("- none identified in verified records")
    add("")

    # ---- 5. Recommendations ---------------------------------------------
    add("## 5. Recommendations\n")
    add("*Each recommendation must trace to a findings section by number. Label each as "
        "(a) supported by verified evidence, (b) supported by reported-but-unverified "
        "patterns warranting investigation, or (c) precautionary. This section is left "
        "for the human author — recommendations are judgment, and judgment is not "
        "machine-draftable in this workflow.*\n")

    # ---- 6. Appendices ----------------------------------------------------
    add("## 6. Appendices\n")
    add("### Appendix A — Methodology")
    add("Fixed 12-tag classification; structured extraction; invariant `human_verified` "
        "starts False; verification requires a typed note; audit log is append-only and "
        "hash-chained (`python src/verify.py --check-audit`).\n")
    add("### Appendix B — Verified source index")
    add("| # | Date | Outlet | Title | Tags |")
    add("|---|---|---|---|---|")
    for i, rec in enumerate(sorted(verified, key=lambda r: r.get("metadata", {}).get("date", ""))):
        meta = rec.get("metadata", {})
        add(f"| {i+1} | {meta.get('date','')} | {meta.get('outlet','')} | "
            f"{meta.get('title','')[:70]} | {', '.join(t for t in rec.get('tags', []) if not t.startswith('SCOPE'))} |")
    add("")
    if all_records is not None:
        unverified = [r for r in all_records
                      if not r.get("extraction_provenance", {}).get("human_verified")]
        add("### Appendix C — Reported but unverified (NOT evidence)")
        add(f"{len(unverified)} extraction record(s) await verification and are excluded "
            f"from every section above. Titles only:\n")
        for rec in unverified[:40]:
            meta = rec.get("metadata", {})
            add(f"- {meta.get('date','')} — {meta.get('outlet','')}: {meta.get('title','')}")
        if len(unverified) > 40:
            add(f"- …and {len(unverified) - 40} more")
        add("")
    add("### Appendix D — Key verified quotes")
    for rec in verified:
        for q in (rec.get("direct_quotes") or [])[:2]:
            add(f'- "{q.get("quote","")}" — {q.get("speaker","?")} ({_cite(rec)})')
    add("")
    return "\n".join(L)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate advocacy report from verified records")
    parser.add_argument("--verified", required=True, help="verified.json from verify.py")
    parser.add_argument("--all-records", default=None,
                        help="extracted.jsonl (optional, for Appendix C unverified counts)")
    parser.add_argument("--output", default="outputs/policy_report.md")
    args = parser.parse_args()

    verified = _load(Path(args.verified))
    leaks = [r for r in verified
             if r.get("extraction_provenance", {}).get("human_verified") is not True]
    if leaks:
        raise SystemExit(
            f"REFUSING to generate: {len(leaks)} record(s) in --verified are not "
            f"human_verified. Re-export via src/verify.py."
        )
    all_records = _load(Path(args.all_records)) if args.all_records else None

    report = generate_report(verified, all_records)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    print(f"Report written -> {output}  ({len(verified)} verified records)")


if __name__ == "__main__":
    main()
