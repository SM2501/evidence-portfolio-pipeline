Evidence Portfolio Pipeline — v2

A computational journalism and policy research pipeline that extracts structured, auditable evidence from news articles and PDFs using a locally hosted LLM (Llama 4 Scout via vLLM). The system classifies documents into a strict 12‑tag framework, stores provisional relationships in Neo4j, and enforces a cryptographically signed human‑in‑the‑loop verification layer before any machine‑generated claim becomes citable.

This upload contains the fully integrated v2 release (evidence-portfolio-pipeline-v2.zip), the recommended download. It bundles 18 modules — including chunking, JSON repair, OCR fallback, adaptive rate‑control, parameterized Neo4j loading, hash‑chained audit signing, and SQLite/WAL checkpointing — all validated by an 18‑check test suite that includes a genuine end‑to‑end mock‑client run over a 3‑document corpus.

The pipeline is resumable, concurrency‑safe, and ready for a pilot calibration run before processing the full 18,000‑article target corpus. Setup instructions, architecture notes, bias‑measurement commitments, and ethical‑use disclosures are included in the README inside the zip.

Earlier files (v1 archive, framework documents, the HHS public comment) are preserved for reference; for actual deployment, use the v2 zip.
