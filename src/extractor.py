"""
extractor.py — Deep evidence extraction using the framework's template (§ Output 3).

The extraction schema is HARD-CODED as the system prompt. Regardless of what
the model returns, extraction_provenance is FORCED to machine defaults in
code afterwards — the model is the first fence, this module is the second.
Only src/verify.py can flip human_verified.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from llm_client import LLMClient, chunk_text, count_tokens

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"

EXTRACTION_SYSTEM_PROMPT = """
You are an evidence-extraction analyst for a drug-policy research portfolio. Read ONE
news article and produce a structured DRAFT extraction record as a single valid JSON
object — no markdown fences, no preamble. You are drafting for human review; nothing
you output is final or verified.

OUTPUT SHAPE (exact keys):
{
  "core_summary": "2-3 sentences: what happened, to whom, and why it matters for policy.",
  "key_individuals": [
    {"name": "", "role": "patient|family|clinician|official|researcher|advocate|industry",
     "affiliation": "", "position_taken": ""}
  ],
  "key_data_points": [
    {"datum": "specific number/date/dosage/count/facility, e.g. 'tapered from 180 to 0 MME in 21 days'",
     "source_in_article": "who or what the article attributes it to",
     "verifiable_against": "primary-source URL ONLY if the article itself links one, else null"}
  ],
  "direct_quotes": [
    {"quote": "verbatim from the article", "speaker": "", "context": "one sentence of setting"}
  ],
  "policy_relevance": {
    "advocacy_claims_supported": ["..."],
    "advocacy_claims_undermined": ["... or 'none identified'"],
    "policy_events_referenced": ["e.g. CDC_2016_GUIDELINE, CDC_2022_REVISION, VA_OSI_2013,
                                  FDA_TAPER_WARNING_2019, DEA_KRATOM_NOTICE_2016,
                                  STATE_MME_CAP, STATE_KRATOM_BAN, STATE_KCPA, FDA_7OH_ACTION_2025"]
  }
}

HARD RULES:
1. Never fabricate. No invented names, quotes, numbers, URLs, or corroboration. If the
   article does not say it, do not write it. Empty lists are acceptable.
2. key_data_points must be SPECIFIC (numbers, dates, dosages, counts, facility names).
   "Many patients were affected" is not a data point.
3. 2-5 direct_quotes maximum, each with speaker and context so it cannot be orphaned
   from attribution.
4. "verifiable_against" is null unless the ARTICLE ITSELF links the primary source.
5. advocacy_claims_undermined may not be empty: if nothing cuts against the access-
   advocacy position, write exactly "none identified". Honesty over advocacy.
6. Stay within the article. Do not import outside knowledge as if the article
   contained it.
""".strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _force_provenance(record: dict) -> dict:
    """Framework invariant: machine output can never arrive pre-verified."""
    record["extraction_provenance"] = {
        "extracted_by": "machine_drafted",
        "extraction_date": _now_iso(),
        "human_verified": False,
        "verification_note": None,
        "schema_version": SCHEMA_VERSION,
    }
    return record


def _merge_chunk_results(parts: list[dict]) -> dict:
    """Union list fields across chunks of one long document; first summary wins."""
    merged: dict[str, Any] = {
        "core_summary": "",
        "key_individuals": [],
        "key_data_points": [],
        "direct_quotes": [],
        "policy_relevance": {
            "advocacy_claims_supported": [],
            "advocacy_claims_undermined": [],
            "policy_events_referenced": [],
        },
    }
    seen: dict[str, set] = {"ind": set(), "dp": set(), "q": set()}
    for part in parts:
        if not merged["core_summary"]:
            merged["core_summary"] = part.get("core_summary", "")
        for ind in part.get("key_individuals", []) or []:
            key = str(ind.get("name", "")).lower()
            if key and key not in seen["ind"]:
                seen["ind"].add(key)
                merged["key_individuals"].append(ind)
        for dp in part.get("key_data_points", []) or []:
            key = str(dp.get("datum", "")).lower()
            if key and key not in seen["dp"]:
                seen["dp"].add(key)
                merged["key_data_points"].append(dp)
        for q in (part.get("direct_quotes", []) or [])[:5]:
            key = str(q.get("quote", ""))[:80].lower()
            if key and key not in seen["q"]:
                seen["q"].add(key)
                merged["direct_quotes"].append(q)
        pr = part.get("policy_relevance", {}) or {}
        for field in merged["policy_relevance"]:
            for item in pr.get(field, []) or []:
                if item not in merged["policy_relevance"][field]:
                    merged["policy_relevance"][field].append(item)
    merged["direct_quotes"] = merged["direct_quotes"][:5]
    und = merged["policy_relevance"]["advocacy_claims_undermined"]
    if len(und) > 1 and "none identified" in und:
        merged["policy_relevance"]["advocacy_claims_undermined"] = [u for u in und if u != "none identified"]
    return merged


def extract_evidence(llm: LLMClient, text: str, metadata: dict, tags: list[str],
                     max_input_tokens: int = 8000, overlap: int = 400) -> dict[str, Any]:
    """
    Extract one evidence record. Long documents are chunked and results merged.
    Raises ValueError if every chunk fails (caller logs and skips the document).
    """
    chunks = (
        chunk_text(text, max_tokens=max_input_tokens, overlap=overlap)
        if count_tokens(text) > max_input_tokens else [text]
    )

    parts: list[dict] = []
    for i, chunk in enumerate(chunks):
        prompt = (
            f"ARTICLE METADATA:\n"
            f"  title: {metadata.get('title', '')}\n"
            f"  outlet: {metadata.get('outlet', '')}\n"
            f"  date: {metadata.get('date', '')}\n"
            f"  assigned_tags: {tags}\n"
            f"  (chunk {i + 1} of {len(chunks)})\n\n"
            f"ARTICLE TEXT:\n{chunk}\n\n"
            f"Produce the extraction record now. Output only the JSON object."
        )
        try:
            part = llm.call_llm(prompt, system_prompt=EXTRACTION_SYSTEM_PROMPT, json_mode=True)
            if isinstance(part, dict):
                parts.append(part)
        except ValueError as exc:
            logger.warning("Extraction chunk %d/%d failed for %s: %s",
                           i + 1, len(chunks), metadata.get("id"), exc)

    if not parts:
        raise ValueError(f"All {len(chunks)} extraction chunk(s) failed for {metadata.get('id')}")

    record = _merge_chunk_results(parts) if len(parts) > 1 else parts[0]
    record["metadata"] = {
        "article_id": metadata.get("id", ""),
        "title": metadata.get("title", ""),
        "outlet": metadata.get("outlet", ""),
        "date": metadata.get("date", ""),
        "url": metadata.get("url", ""),
        "scope": next((t for t in tags if t.startswith("SCOPE_")), ""),
    }
    record["tags"] = tags
    return _force_provenance(record)
