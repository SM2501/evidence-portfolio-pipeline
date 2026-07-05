"""
classifier.py — First-pass tag triage using the framework's 12-tag system.

The tag set and definitions are HARD-CODED constants (framework §2.1) so the
schema cannot drift out from under stored classifications. If you change a
definition, bump SCHEMA_VERSION and re-run affected articles.
"""

from __future__ import annotations

import logging
from typing import Any

from llm_client import LLMClient, chunk_text, count_tokens

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"

# ---------------------------------------------------------------
# Framework §2.1 — exact tag definitions (do not paraphrase)
# ---------------------------------------------------------------
TAG_DEFINITIONS: dict[str, str] = {
    "TAPER_HARM": (
        "Documented adverse outcome attributed to dose reduction or discontinuation. "
        "Apply when the article names a harm (withdrawal, destabilization, overdose, "
        "suicide, ER visit) causally linked to a taper."
    ),
    "VETERAN_SUICIDE": (
        "Veteran suicide or suicidal ideation in a pain-care/VA context. Apply when the "
        "article involves a veteran AND suicide/ideation; co-tag with TAPER_HARM if taper-linked."
    ),
    "GUIDELINE_CRITIQUE": (
        "Challenge to the evidence base or application of CDC/VA/state guidelines. Apply when "
        "the article quotes critics, cites contrary studies, or reports guideline 'misapplication'."
    ),
    "GUIDELINE_DEFENSE": (
        "Official or expert defense of restrictive guidelines/policies. Apply when the article "
        "quotes officials or researchers defending restrictions (counter-evidence tag)."
    ),
    "KRATOM_POLICY": (
        "Kratom or 7-OH regulatory action or debate. Apply when the article covers bans, "
        "KCPAs, scheduling attempts, FDA/DEA actions, import alerts."
    ),
    "PATIENT_ABANDONMENT": (
        "Loss of care access without clinical justification. Apply for prescriber closure/raid, "
        "patient dismissal, refusal to accept legacy patients."
    ),
    "STIGMA": (
        "Stigmatizing framing or documented discrimination. Apply for 'drug-seeker' labeling, "
        "chart flags, dismissive treatment, or loaded language in the article itself."
    ),
    "DISPLACEMENT": (
        "Movement to illicit or gray-market supply after legal access ended. Apply for "
        "prescription -> street opioids, or prescription -> kratom/7-OH substitution."
    ),
    "DATA_LIMITATION": (
        "Critique or misuse of surveillance data. Apply for FAERS, NPDS, PDMP scores, "
        "toxicology attribution, missing denominators."
    ),
    "PERSONAL_ACCOUNT": (
        "First-person or family-member narrative is central. Apply when the story is built "
        "around one identifiable patient/family (vs. aggregate reporting)."
    ),
    "POLICY_TENSION": (
        "Conflict between policies, agencies, or stated goals. E.g., FDA taper warning vs. "
        "state MME caps; addiction-treatment goals vs. pain-care goals."
    ),
    "SCOPE": (
        "Geographic scope pair: SCOPE_LOCAL (local/regional outlet or single-state focus) vs. "
        "SCOPE_NATIONAL (national outlet/multi-state framing). Exactly one is required, and "
        "scope follows the FRAMING, not the dateline."
    ),
}

CONTENT_TAGS: frozenset[str] = frozenset(TAG_DEFINITIONS) - {"SCOPE"}
SCOPE_TAGS: frozenset[str] = frozenset({"SCOPE_LOCAL", "SCOPE_NATIONAL"})
ALL_TAGS: frozenset[str] = CONTENT_TAGS | SCOPE_TAGS

STANCE_VALUES = ("access_sympathetic", "restriction_sympathetic", "mixed", "neutral_report")
CONFIDENCE_VALUES = ("high", "medium", "low")


def _render_tag_block() -> str:
    lines = [f"- {tag}: {definition}" for tag, definition in TAG_DEFINITIONS.items() if tag != "SCOPE"]
    lines.append(f"- SCOPE_LOCAL / SCOPE_NATIONAL: {TAG_DEFINITIONS['SCOPE']}")
    return "\n".join(lines)


CLASSIFIER_SYSTEM_PROMPT = f"""
You are a media-analysis classifier for a drug-policy research dataset. You apply a
fixed 12-tag classification framework to ONE news article and output ONLY a valid
JSON object — no markdown, no preamble.

TAG DEFINITIONS (fixed; do not invent tags):
{_render_tag_block()}

RULES (all mandatory):
1. Tag from the article text ONLY. If justification requires outside knowledge, do
   not apply the tag.
2. EVERY applied tag requires a one-sentence evidence entry in "tag_evidence",
   quoting or closely paraphrasing what in the article justifies it. A tag without
   evidence is invalid.
3. Multi-tagging is expected (typically 2-4 content tags). Apply GUIDELINE_DEFENSE
   whenever present — counter-evidence must never be skipped.
4. Include EXACTLY ONE scope tag (SCOPE_LOCAL or SCOPE_NATIONAL) in "tags".
5. "stance_signal" describes the ARTICLE'S framing, not your view:
   one of {list(STANCE_VALUES)}.
6. "confidence" is your classification confidence: one of {list(CONFIDENCE_VALUES)}.
7. If the article is irrelevant to the framework, return "tags" with only the scope
   tag and set "confidence" accordingly.

OUTPUT SHAPE (exact keys):
{{
  "tags": ["TAG", "..."],
  "tag_evidence": {{"TAG": "one-sentence justification", "...": "..."}},
  "stance_signal": "access_sympathetic|restriction_sympathetic|mixed|neutral_report",
  "confidence": "high|medium|low",
  "geography": {{"state": "", "locality": "", "va_facility": ""}}
}}
""".strip()


def classify_article(llm: LLMClient, text: str, metadata: dict,
                     max_input_tokens: int = 8000) -> dict[str, Any]:
    """
    Classify one article. Long articles are truncated to the first chunk —
    classification is triage; deep reading happens in the extractor.
    Raises ValueError on unusable model output (caller logs and skips).
    """
    if count_tokens(text) > max_input_tokens:
        text = chunk_text(text, max_tokens=max_input_tokens, overlap=0)[0]
        logger.debug("Article %s truncated for classification", metadata.get("id"))

    prompt = (
        f"ARTICLE METADATA:\n"
        f"  title: {metadata.get('title', '')}\n"
        f"  outlet: {metadata.get('outlet', '')}\n"
        f"  date: {metadata.get('date', '')}\n\n"
        f"ARTICLE TEXT:\n{text}\n\n"
        f"Classify this article now. Output only the JSON object."
    )
    result = llm.call_llm(prompt, system_prompt=CLASSIFIER_SYSTEM_PROMPT, json_mode=True)
    if not isinstance(result, dict):
        raise ValueError("Classifier returned non-object JSON")

    result["schema_version"] = SCHEMA_VERSION
    result["tagged_by"] = "machine_drafted"
    result["review_status"] = "pending"
    return result
