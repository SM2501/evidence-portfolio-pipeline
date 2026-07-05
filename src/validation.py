"""
validation.py — Schema and invariant validation for classifier/extractor outputs.

Every failure is logged with a specific, actionable message. Validators return
(ok: bool, errors: list[str]) so the orchestrator can count and skip rather
than crash. `enforce_extraction_invariants` additionally REPAIRS the two
non-negotiables in place (human_verified=False, undermined-claims non-empty)
so a misbehaving model can never smuggle verified-looking records downstream.
"""

from __future__ import annotations

import logging
from typing import Any

from classifier import ALL_TAGS, CONFIDENCE_VALUES, SCOPE_TAGS, STANCE_VALUES

logger = logging.getLogger(__name__)

EXTRACTION_REQUIRED_KEYS = {
    "metadata", "core_summary", "key_individuals", "key_data_points",
    "direct_quotes", "policy_relevance", "tags", "extraction_provenance",
}
METADATA_REQUIRED_KEYS = {"article_id", "title", "outlet", "date", "url"}
POLICY_RELEVANCE_KEYS = {
    "advocacy_claims_supported", "advocacy_claims_undermined", "policy_events_referenced",
}


def validate_classification(result: dict, article_id: str = "?") -> tuple[bool, list[str]]:
    errors: list[str] = []

    tags = result.get("tags")
    if not isinstance(tags, list) or not tags:
        errors.append("'tags' missing or not a non-empty list")
        tags = []

    unknown = [t for t in tags if t not in ALL_TAGS]
    if unknown:
        errors.append(f"unknown tag(s) {unknown} — tags are a fixed vocabulary")

    scope = [t for t in tags if t in SCOPE_TAGS]
    if len(scope) != 1:
        errors.append(f"exactly one scope tag required, found {scope or 'none'}")

    evidence = result.get("tag_evidence")
    if not isinstance(evidence, dict):
        errors.append("'tag_evidence' missing or not an object")
    else:
        for tag in tags:
            if tag in SCOPE_TAGS:
                continue
            if not str(evidence.get(tag, "")).strip():
                errors.append(f"tag '{tag}' applied without a tag_evidence justification")

    if result.get("stance_signal") not in STANCE_VALUES:
        errors.append(f"stance_signal must be one of {list(STANCE_VALUES)}")
    if result.get("confidence") not in CONFIDENCE_VALUES:
        errors.append(f"confidence must be one of {list(CONFIDENCE_VALUES)}")

    geography = result.get("geography")
    if not isinstance(geography, dict):
        errors.append("'geography' missing or not an object")

    for err in errors:
        logger.warning("Classification validation [%s]: %s", article_id, err)
    return (not errors), errors


def validate_extraction(record: dict, article_id: str = "?") -> tuple[bool, list[str]]:
    errors: list[str] = []

    missing = EXTRACTION_REQUIRED_KEYS - set(record)
    if missing:
        errors.append(f"missing top-level key(s): {sorted(missing)}")

    meta = record.get("metadata", {})
    if isinstance(meta, dict):
        meta_missing = METADATA_REQUIRED_KEYS - set(meta)
        if meta_missing:
            errors.append(f"metadata missing key(s): {sorted(meta_missing)}")
    else:
        errors.append("'metadata' is not an object")

    for list_field in ("key_individuals", "key_data_points", "direct_quotes", "tags"):
        if not isinstance(record.get(list_field), list):
            errors.append(f"'{list_field}' is not a list")

    if len(record.get("direct_quotes") or []) > 5:
        errors.append("more than 5 direct_quotes (framework cap is 5)")

    pr = record.get("policy_relevance", {})
    if isinstance(pr, dict):
        pr_missing = POLICY_RELEVANCE_KEYS - set(pr)
        if pr_missing:
            errors.append(f"policy_relevance missing key(s): {sorted(pr_missing)}")
        undermined = pr.get("advocacy_claims_undermined")
        if isinstance(undermined, list) and not undermined:
            errors.append("advocacy_claims_undermined is empty — must contain items or 'none identified'")
    else:
        errors.append("'policy_relevance' is not an object")

    prov = record.get("extraction_provenance", {})
    if isinstance(prov, dict):
        if prov.get("human_verified") is not False:
            errors.append("INVARIANT: extraction_provenance.human_verified must start as False")
        if prov.get("extracted_by") != "machine_drafted":
            errors.append("INVARIANT: extracted_by must start as 'machine_drafted'")
    else:
        errors.append("'extraction_provenance' is not an object")

    for err in errors:
        logger.warning("Extraction validation [%s]: %s", article_id, err)
    return (not errors), errors


def enforce_extraction_invariants(record: dict) -> dict:
    """
    Repair (not just report) the two trust-layer non-negotiables. Called on
    every record before persistence, regardless of validation outcome.
    """
    prov = record.setdefault("extraction_provenance", {})
    if prov.get("human_verified") is not False or prov.get("verification_note") is not None:
        logger.warning(
            "Record %s arrived with pre-set verification — forcing back to unverified.",
            record.get("metadata", {}).get("article_id", "?"),
        )
    prov["human_verified"] = False
    prov["verification_note"] = None
    prov.setdefault("extracted_by", "machine_drafted")

    pr = record.setdefault("policy_relevance", {})
    if not pr.get("advocacy_claims_undermined"):
        pr["advocacy_claims_undermined"] = ["none identified"]
    return record
