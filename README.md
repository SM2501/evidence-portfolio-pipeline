## UPDATE: Pipeline V2 Architecture Audit & Redesign (WIP)
**Date:** July 2026

The attached `.zip` archive contains a comprehensive architectural audit and module redesign of the original evidence extraction pipeline, generated via Claude Fable 5. 

While the original scripts functioned as a sequential, proof-of-concept ETL, this redesign transitions the architecture to an enterprise-grade, high-throughput pipeline built for a local vLLM endpoint (H100/Llama 4 Scout). 

**Key Upgrades in this Archive:**
*   **Asynchronous Processing:** Transitioned from a single-threaded loop to an `asyncio` producer/consumer model.
*   **Adaptive Rate Controlling:** Implementation of an AIMD (Additive-Increase/Multiplicative-Decrease) concurrency gate to prevent vLLM queue meltdown under heavy load.
*   **Cryptographic Trust Layer:** Upgraded the human-in-the-loop verification step to use an HMAC-signed, hash-chained audit log, ensuring absolute immutability of reviewer decisions.
*   **Injection-Safe Graph Loading:** Transitioned Neo4j ingestion to use dynamic, byte-aware batching and fully parameterized `UNWIND` queries to prevent Cypher injection.
*   **Chunk-Level Checkpointing:** Replaced the JSON ledger with a WAL-mode SQLite database for precise, crash-safe resumability.

**Note on Status:** This is a Work-In-Progress (WIP). The archive contains the fully tested standalone modules (cleaning, JSON repair, rate controlling, Neo4j loading, etc.) and the deep architectural audit. The final monolithic integration scripts (the "glue" binding these modules together) are pending final generation.

# Evidence Portfolio Pipeline

Machine-assisted evidence extraction from ~18,000 Media Cloud news articles
(plus research-paper PDFs) into a Neo4j knowledge graph and a human-verified
policy advocacy report. Implements the 12-tag classification framework, the
structured extraction template, the 42 exploratory questions as Cypher, and a
trust layer in which **nothing machine-generated is citable until a human
verifies it with a typed note**.

## Architecture

```
Media Cloud CSV/JSON ─┐
                      ├─> article_ingestor / pdf_parser
PDFs (papers/reports)─┘        │
                               v
                    classifier (12 fixed tags)
                               │
                               v
                    extractor (framework template)
                               │
                     validation + invariants        <- human_verified forced False
                               │
              ┌────────────────┴───────────────┐
              v                                v
   outputs/extracted.jsonl              Neo4j graph (provisional)
              │                          + 42 Cypher queries
              v
        src/verify.py  <- the ONLY path that flips human_verified
              │            (typed note, hash-chained audit log)
              v
   outputs/verified.json ──> report_generator ──> policy_report.md
```

## 1. Prerequisites

- Python 3.10+
- A RunPod account with one available H100 80GB (or any GPU that fits your model choice)
- Neo4j: local Docker (`docker run -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/yourpass neo4j:5`) or a free AuraDB instance
- A Hugging Face token with access to the gated Llama 4 repo

## 2. Deploy the model on RunPod

Start a pod (1x H100 80GB, a PyTorch/CUDA template is fine), open its web
terminal, copy `runpod_deploy.sh` in, then:

```bash
export HF_TOKEN=hf_xxx VLLM_API_KEY=pick-any-secret
bash runpod_deploy.sh
```

**Read the notes at the top of that script before running.** Two spec items
were adjusted to things that actually run on one H100:

- The famous "1.78-bit" Llama 4 Scout builds are Unsloth **GGUF** quants, which
  vLLM does not serve reliably for MoE models. The script defaults to the
  official **FP8** Scout build (H100-native). If you specifically want the
  1.78-bit GGUF, serve it with `llama.cpp`'s `llama-server` instead — it is
  also OpenAI-compatible, so every client in this repo works unchanged.
- `--max-model-len` defaults to **65536**, not 262144: a 262k KV cache next to
  Scout's weights will very likely OOM a single 80GB card, and this pipeline
  chunks inputs to ~8k tokens anyway. Override with `MAX_MODEL_LEN=262144` if
  you want to try.

## 3. Configure

```bash
cp .env.example .env      # fill in RunPod endpoint/key/model + Neo4j creds
```

`config.yaml` holds non-secret settings (batch sizes, chunking, paths) and
resolves `${VARS}` from `.env`. Never commit `.env` (already gitignored).

## 4. Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 5. Run the pipeline (batch-at-a-time)

```bash
# First 100 articles tonight:
python src/pipeline_orchestrator.py --articles data/articles.csv --limit 100

# Next 500 tomorrow — the ledger skips everything already done:
python src/pipeline_orchestrator.py --articles data/articles.csv --limit 500

# PDFs of research papers / reports:
python src/pipeline_orchestrator.py --pdf-dir data/pdfs --limit 25

# Also load this run into Neo4j:
python src/pipeline_orchestrator.py --articles data/articles.csv --limit 100 --load-graph

# Reattempt previously failed documents:
python src/pipeline_orchestrator.py --articles data/articles.csv --retry-failed
```

Resumability lives in `outputs/processed_ledger.json`; extraction records
append to `outputs/extracted.jsonl`. Interrupting a run is safe.

## 6. Verify extractions (the trust layer)

```bash
python src/verify.py --input outputs/extracted.jsonl
python src/verify.py --check-audit          # audit-log tamper check
```

Open the article URL yourself before pressing `y`; the typed note is your
testimony and goes into a hash-chained, append-only audit log.

## 7. Generate the report (verified-only)

```bash
python src/report_generator.py --verified outputs/verified.json \
    --all-records outputs/extracted.jsonl
```

The generator hard-refuses if any record in `--verified` is not
`human_verified: true`. Unverified material appears only in Appendix C,
labeled "NOT evidence".

## 8. Query the graph (42 framework questions)

```bash
python src/neo4j_queries.py --list      # all 42 questions
python src/neo4j_queries.py Q22         # VA facilities with taper-harm coverage
python src/neo4j_queries.py Q29         # kratom ban vs KCPA states
```

Graph results are `epistemic_status: llm_provisional` — they nominate reading,
they are never citable. The quotability gate is a whitelist of verified
statuses, never a blacklist.

## Pushing to a private GitHub repo

```bash
cd evidence-portfolio-pipeline
git init
git add .
git commit -m "Initial pipeline"
gh repo create evidence-portfolio-pipeline --private --source=. --push
# or without gh CLI:
#   create the private repo in the GitHub UI, then:
#   git remote add origin git@github.com:<you>/evidence-portfolio-pipeline.git
#   git branch -M main && git push -u origin main
```

`.gitignore` already excludes `.env`, `outputs/`, `data/`, and virtualenvs —
verify with `git status` that no secrets or datasets are staged before the
first push.

## Repository layout

```
├── README.md
├── requirements.txt
├── config.yaml            # non-secret settings; ${VARS} resolved from .env
├── .env.example           # copy to .env (gitignored)
├── .gitignore
├── runpod_deploy.sh       # vLLM server on the pod
├── data/                  # your CSV/JSON exports and PDFs (gitignored)
├── outputs/               # ledger, extractions, audit log, report (gitignored)
└── src/
    ├── llm_client.py            # OpenAI-compatible vLLM client, retries, chunking
    ├── pdf_parser.py            # pymupdf extraction + chunking
    ├── article_ingestor.py      # Media Cloud CSV/JSON normalization
    ├── classifier.py            # 12 tags hard-coded; first-pass triage
    ├── extractor.py             # framework template hard-coded; provenance forced
    ├── validation.py            # schema + invariant checks/repairs
    ├── neo4j_client.py          # constraints + UNWIND batch loading
    ├── neo4j_queries.py         # all 42 questions as Cypher
    ├── pipeline_orchestrator.py # CLI, batching, ledger, resumability
    ├── verify.py                # human trust layer + hash-chained audit log
    └── report_generator.py      # verified-only advocacy report
```

## Cost note (RunPod on-demand)

An H100 pod bills per hour while running. Classification + extraction is
roughly 2 LLM calls per article; at typical vLLM throughput for a Scout-class
model, expect on the order of 1–3k articles/hour, so budget pod-hours
accordingly and **stop the pod between batch sessions** — the ledger makes
stopping free.
