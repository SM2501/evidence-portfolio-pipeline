"""
neo4j_client.py — Graph loading layer.

Graph model (all machine-derived content is provisional by construction):

  (:Article {id, title, outlet, date, url, scope, stance_signal, confidence,
             tags: [..], state, locality, va_facility, core_summary,
             human_verified, epistemic_status})
  (:Person    {name, role, affiliation})
  (:Policy    {name})
  (:Substance {name})
  (:DataPoint {key, datum, source_in_article, verifiable_against})
  (:Quote     {key, text, speaker, context})

  (Article)-[:MENTIONS]->(Person|Substance)
  (Article)-[:REPORTS_ON]->(Policy)
  (Article)-[:CITES]->(DataPoint|Quote)
  (Person {role:'patient'|'family'})-[:AFFECTED_BY]->(Policy)   heuristic*
  (Policy)-[:LEADS_TO]->(Article)                                heuristic*
  (Article)-[:DISPLACES]->(Substance)                            heuristic*

  * Heuristic edges are created only when tag evidence supports them and are
    stamped {derivation:'heuristic', epistemic_status:'llm_provisional'} —
    exploration compass, never citable fact (framework whitelist rule).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Iterable

from neo4j import GraphDatabase, Driver

logger = logging.getLogger(__name__)

BATCH_SIZE = 500

SUBSTANCE_KEYWORDS: dict[str, list[str]] = {
    "Kratom": ["kratom", "mitragyna", "mitragynine"],
    "7-OH": ["7-oh", "7-hydroxymitragynine", "7oh"],
    "Fentanyl": ["fentanyl"],
    "Heroin": ["heroin"],
    "Oxycodone": ["oxycodone", "oxycontin", "percocet"],
    "Hydrocodone": ["hydrocodone", "vicodin"],
    "Morphine": ["morphine"],
    "Methadone": ["methadone"],
    "Buprenorphine": ["buprenorphine", "suboxone", "subutex"],
    "Naloxone": ["naloxone", "narcan"],
}

CONSTRAINTS = [
    "CREATE CONSTRAINT article_id  IF NOT EXISTS FOR (a:Article)   REQUIRE a.id   IS UNIQUE",
    "CREATE CONSTRAINT person_name IF NOT EXISTS FOR (p:Person)    REQUIRE p.name IS UNIQUE",
    "CREATE CONSTRAINT policy_name IF NOT EXISTS FOR (p:Policy)    REQUIRE p.name IS UNIQUE",
    "CREATE CONSTRAINT subst_name  IF NOT EXISTS FOR (s:Substance) REQUIRE s.name IS UNIQUE",
    "CREATE CONSTRAINT dp_key      IF NOT EXISTS FOR (d:DataPoint) REQUIRE d.key  IS UNIQUE",
    "CREATE CONSTRAINT quote_key   IF NOT EXISTS FOR (q:Quote)     REQUIRE q.key  IS UNIQUE",
]


def _key(*parts: str) -> str:
    return hashlib.sha256("::".join(parts).encode()).hexdigest()[:24]


def _detect_substances(record: dict) -> list[str]:
    haystack = " ".join([
        str(record.get("core_summary", "")),
        " ".join(str(dp.get("datum", "")) for dp in record.get("key_data_points", []) or []),
        str(record.get("metadata", {}).get("title", "")),
    ]).lower()
    return [name for name, kws in SUBSTANCE_KEYWORDS.items() if any(kw in haystack for kw in kws)]


class Neo4jClient:
    def __init__(self, config: dict):
        cfg = config["neo4j"]
        self.database: str = cfg.get("database", "neo4j")
        self._driver: Driver = GraphDatabase.driver(
            cfg["uri"],
            auth=(cfg["user"], cfg["password"]),
            max_connection_pool_size=20,
            connection_acquisition_timeout=30.0,
        )
        self._driver.verify_connectivity()
        logger.info("Connected to Neo4j at %s (db=%s)", cfg["uri"], self.database)

    def close(self) -> None:
        self._driver.close()

    def __enter__(self) -> "Neo4jClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def create_constraints(self) -> None:
        with self._driver.session(database=self.database) as session:
            for statement in CONSTRAINTS:
                session.run(statement)
        logger.info("Constraints ensured (%d)", len(CONSTRAINTS))

    # ------------------------------------------------------------------
    def build_graph(self, extracted_records: Iterable[dict]) -> dict[str, int]:
        """Transform extraction records into rows and bulk-load with UNWIND."""
        articles, persons, policies, substances = [], [], [], []
        datapoints, quotes = [], []
        r_mentions_p, r_reports, r_mentions_s = [], [], []
        r_cites_dp, r_cites_q, r_affected, r_leads, r_displaces = [], [], [], [], []

        for rec in extracted_records:
            meta = rec.get("metadata", {})
            aid = meta.get("article_id")
            if not aid:
                logger.warning("Skipping record without article_id")
                continue
            cls = rec.get("classification", {})
            geo = cls.get("geography", {}) or {}
            tags = rec.get("tags", []) or []
            prov = rec.get("extraction_provenance", {}) or {}

            articles.append({
                "id": aid,
                "title": meta.get("title", ""),
                "outlet": meta.get("outlet", ""),
                "date": meta.get("date", ""),
                "url": meta.get("url", ""),
                "scope": meta.get("scope", ""),
                "tags": tags,
                "stance_signal": cls.get("stance_signal", ""),
                "confidence": cls.get("confidence", ""),
                "state": geo.get("state", ""),
                "locality": geo.get("locality", ""),
                "va_facility": geo.get("va_facility", ""),
                "core_summary": rec.get("core_summary", ""),
                "human_verified": bool(prov.get("human_verified", False)),
                "epistemic_status": "llm_provisional",
            })

            for ind in rec.get("key_individuals", []) or []:
                name = str(ind.get("name", "")).strip()
                if not name:
                    continue
                persons.append({
                    "name": name,
                    "role": str(ind.get("role", "")),
                    "affiliation": str(ind.get("affiliation", "") or ""),
                })
                r_mentions_p.append({"aid": aid, "name": name,
                                     "position": str(ind.get("position_taken", "") or "")})

            pr = rec.get("policy_relevance", {}) or {}
            for policy in pr.get("policy_events_referenced", []) or []:
                policy = str(policy).strip()
                if not policy:
                    continue
                policies.append({"name": policy})
                r_reports.append({"aid": aid, "name": policy})
                if "TAPER_HARM" in tags or "PATIENT_ABANDONMENT" in tags:
                    r_leads.append({"name": policy, "aid": aid})
                    for ind in rec.get("key_individuals", []) or []:
                        if str(ind.get("role", "")) in {"patient", "family"} and ind.get("name"):
                            r_affected.append({"pname": str(ind["name"]).strip(), "polname": policy})

            for sub in _detect_substances(rec):
                substances.append({"name": sub})
                r_mentions_s.append({"aid": aid, "name": sub})
                if "DISPLACEMENT" in tags and sub != "Naloxone":
                    r_displaces.append({"aid": aid, "name": sub})

            for dp in rec.get("key_data_points", []) or []:
                datum = str(dp.get("datum", "")).strip()
                if not datum:
                    continue
                key = _key(aid, datum)
                datapoints.append({
                    "key": key, "datum": datum,
                    "source_in_article": str(dp.get("source_in_article", "") or ""),
                    "verifiable_against": dp.get("verifiable_against") or "",
                })
                r_cites_dp.append({"aid": aid, "key": key})

            for q in rec.get("direct_quotes", []) or []:
                text = str(q.get("quote", "")).strip()
                if not text:
                    continue
                key = _key(aid, text[:120])
                quotes.append({
                    "key": key, "text": text,
                    "speaker": str(q.get("speaker", "") or ""),
                    "context": str(q.get("context", "") or ""),
                })
                r_cites_q.append({"aid": aid, "key": key})

        counts: dict[str, int] = {}
        with self._driver.session(database=self.database) as session:
            plan: list[tuple[str, list, str]] = [
                ("Article", articles,
                 "UNWIND $rows AS r MERGE (a:Article {id:r.id}) SET a += r"),
                ("Person", persons,
                 "UNWIND $rows AS r MERGE (p:Person {name:r.name}) "
                 "SET p.role = coalesce(r.role, p.role), "
                 "    p.affiliation = coalesce(nullIf(r.affiliation,''), p.affiliation)"),
                ("Policy", policies,
                 "UNWIND $rows AS r MERGE (:Policy {name:r.name})"),
                ("Substance", substances,
                 "UNWIND $rows AS r MERGE (:Substance {name:r.name})"),
                ("DataPoint", datapoints,
                 "UNWIND $rows AS r MERGE (d:DataPoint {key:r.key}) SET d += r"),
                ("Quote", quotes,
                 "UNWIND $rows AS r MERGE (q:Quote {key:r.key}) SET q += r"),
                ("MENTIONS(Person)", r_mentions_p,
                 "UNWIND $rows AS r MATCH (a:Article {id:r.aid}), (p:Person {name:r.name}) "
                 "MERGE (a)-[m:MENTIONS]->(p) SET m.position_taken = r.position"),
                ("REPORTS_ON", r_reports,
                 "UNWIND $rows AS r MATCH (a:Article {id:r.aid}), (p:Policy {name:r.name}) "
                 "MERGE (a)-[:REPORTS_ON]->(p)"),
                ("MENTIONS(Substance)", r_mentions_s,
                 "UNWIND $rows AS r MATCH (a:Article {id:r.aid}), (s:Substance {name:r.name}) "
                 "MERGE (a)-[:MENTIONS]->(s)"),
                ("CITES(DataPoint)", r_cites_dp,
                 "UNWIND $rows AS r MATCH (a:Article {id:r.aid}), (d:DataPoint {key:r.key}) "
                 "MERGE (a)-[:CITES]->(d)"),
                ("CITES(Quote)", r_cites_q,
                 "UNWIND $rows AS r MATCH (a:Article {id:r.aid}), (q:Quote {key:r.key}) "
                 "MERGE (a)-[:CITES]->(q)"),
                ("AFFECTED_BY*", r_affected,
                 "UNWIND $rows AS r MATCH (p:Person {name:r.pname}), (pol:Policy {name:r.polname}) "
                 "MERGE (p)-[e:AFFECTED_BY]->(pol) "
                 "SET e.derivation='heuristic', e.epistemic_status='llm_provisional'"),
                ("LEADS_TO*", r_leads,
                 "UNWIND $rows AS r MATCH (pol:Policy {name:r.name}), (a:Article {id:r.aid}) "
                 "MERGE (pol)-[e:LEADS_TO]->(a) "
                 "SET e.derivation='heuristic', e.epistemic_status='llm_provisional'"),
                ("DISPLACES*", r_displaces,
                 "UNWIND $rows AS r MATCH (a:Article {id:r.aid}), (s:Substance {name:r.name}) "
                 "MERGE (a)-[e:DISPLACES]->(s) "
                 "SET e.derivation='heuristic', e.epistemic_status='llm_provisional'"),
            ]
            for label, rows, cypher in plan:
                total = 0
                for i in range(0, len(rows), BATCH_SIZE):
                    batch = rows[i:i + BATCH_SIZE]
                    session.execute_write(lambda tx, b=batch, c=cypher: tx.run(c, rows=b).consume())
                    total += len(batch)
                counts[label] = total
                if total:
                    logger.info("Loaded %-18s %6d rows", label, total)
        return counts
