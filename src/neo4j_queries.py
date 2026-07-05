"""
neo4j_queries.py — The framework's 42 exploratory questions as executable Cypher.

Usage:
    from neo4j_queries import QUERIES, run_query
    rows = run_query(neo4j_client, "Q22")

    python neo4j_queries.py --list
    python neo4j_queries.py Q22

Notes:
- Queries operate on the provisional graph. Results NOMINATE reading;
  they are never citable until the source article is human-verified.
- Text matching uses toLower(...) CONTAINS. For serious use at 18k articles,
  create a full-text index (see FULLTEXT_SETUP below) and swap the CONTAINS
  clauses for db.index.fulltext.queryNodes calls.
"""

from __future__ import annotations

FULLTEXT_SETUP = """
CREATE FULLTEXT INDEX article_text IF NOT EXISTS
FOR (a:Article) ON EACH [a.title, a.core_summary]
"""

_RETURN_ARTICLE = (
    "RETURN a.id AS id, a.date AS date, a.outlet AS outlet, a.title AS title, "
    "a.state AS state, a.url AS url ORDER BY a.date"
)

QUERIES: dict[str, dict[str, str]] = {

    # ---------- A. Patient Stories and Human Impact ----------
    "Q1": {
        "question": "Articles naming veterans/patients who died by suicide or had suicidal ideation after an involuntary taper.",
        "cypher": f"""
MATCH (a:Article) WHERE 'VETERAN_SUICIDE' IN a.tags AND 'TAPER_HARM' IN a.tags
OPTIONAL MATCH (a)-[:MENTIONS]->(p:Person) WHERE p.role IN ['patient','family']
WITH a, collect(DISTINCT p.name) AS named_people
RETURN a.id AS id, a.date AS date, a.outlet AS outlet, a.title AS title,
       a.va_facility AS va_facility, named_people, a.url AS url ORDER BY a.date""",
    },
    "Q2": {
        "question": "First-person/family accounts of being 'cut off' — notification method and aftermath.",
        "cypher": f"""
MATCH (a:Article) WHERE 'PERSONAL_ACCOUNT' IN a.tags
  AND ('TAPER_HARM' IN a.tags OR 'PATIENT_ABANDONMENT' IN a.tags)
OPTIONAL MATCH (a)-[:CITES]->(q:Quote)
WITH a, collect(q.text)[..3] AS sample_quotes
RETURN a.id AS id, a.date AS date, a.title AS title, sample_quotes, a.url AS url ORDER BY a.date""",
    },
    "Q3": {
        "question": "Patients turning to illicit fentanyl/heroin/street pills after a legal prescription ended (incl. toxicology).",
        "cypher": f"""
MATCH (a:Article)-[:DISPLACES|MENTIONS]->(s:Substance)
WHERE 'DISPLACEMENT' IN a.tags AND s.name IN ['Fentanyl','Heroin','Oxycodone']
OPTIONAL MATCH (a)-[:CITES]->(d:DataPoint) WHERE toLower(d.datum) CONTAINS 'toxicolog'
RETURN DISTINCT a.id AS id, a.date AS date, a.title AS title, s.name AS substance,
       collect(d.datum) AS toxicology_data, a.url AS url ORDER BY a.date""",
    },
    "Q4": {
        "question": "Accounts of kratom/7-OH used as replacement after losing prescription access, with outcomes.",
        "cypher": f"""
MATCH (a:Article)-[:MENTIONS]->(s:Substance) WHERE s.name IN ['Kratom','7-OH']
  AND 'DISPLACEMENT' IN a.tags
{_RETURN_ARTICLE}""",
    },
    "Q5": {
        "question": "Patients with objectively documented conditions (CRPS, arachnoiditis, sickle cell, cancer) tapered under mismatched policies.",
        "cypher": f"""
MATCH (a:Article) WHERE 'TAPER_HARM' IN a.tags AND any(kw IN
  ['crps','arachnoiditis','sickle cell','cancer','post-surgical','ehlers']
  WHERE toLower(a.core_summary) CONTAINS kw OR toLower(a.title) CONTAINS kw)
{_RETURN_ARTICLE}""",
    },
    "Q6": {
        "question": "'Pharmacy crawl' stories — fills refused due to wholesaler caps, red-flag algorithms, pharmacist refusal; chains named.",
        "cypher": f"""
MATCH (a:Article) WHERE any(kw IN
  ['pharmacy','pharmacist','walgreens','cvs','wholesaler','red flag','refus']
  WHERE toLower(a.core_summary) CONTAINS kw)
  AND ('PATIENT_ABANDONMENT' IN a.tags OR 'STIGMA' IN a.tags)
{_RETURN_ARTICLE}""",
    },
    "Q7": {
        "question": "Caregiver/family accounts of household impact of a forced taper.",
        "cypher": f"""
MATCH (a:Article)-[:MENTIONS]->(p:Person {{role:'family'}})
WHERE 'TAPER_HARM' IN a.tags
RETURN a.id AS id, a.date AS date, a.title AS title, collect(p.name) AS family_members,
       a.url AS url ORDER BY a.date""",
    },
    "Q8": {
        "question": "Longitudinal follow-ups — the same named person covered in multiple articles over time.",
        "cypher": """
MATCH (a:Article)-[:MENTIONS]->(p:Person)
WHERE p.role IN ['patient','family']
WITH p, collect(DISTINCT {id:a.id, date:a.date, title:a.title}) AS coverage
WHERE size(coverage) > 1
RETURN p.name AS person, size(coverage) AS n_articles, coverage ORDER BY n_articles DESC""",
    },

    # ---------- B. Policy Implementation and Consequences ----------
    "Q9": {
        "question": "States with MME caps / day-supply limits and reported post-implementation outcomes.",
        "cypher": f"""
MATCH (a:Article)-[:REPORTS_ON]->(pol:Policy)
WHERE pol.name CONTAINS 'MME' OR toLower(a.core_summary) CONTAINS 'morphine milligram'
   OR toLower(a.core_summary) CONTAINS 'day supply' OR toLower(a.core_summary) CONTAINS 'prescribing cap'
RETURN a.state AS state, a.id AS id, a.date AS date, a.title AS title, a.url AS url
ORDER BY state, date""",
    },
    "Q10": {
        "question": "VA Opioid Safety Initiative — measurable effects and acknowledged harms.",
        "cypher": f"""
MATCH (a:Article) WHERE toLower(a.core_summary) CONTAINS 'opioid safety initiative'
   OR EXISTS {{ MATCH (a)-[:REPORTS_ON]->(:Policy {{name:'VA_OSI_2013'}}) }}
OPTIONAL MATCH (a)-[:CITES]->(d:DataPoint)
RETURN a.id AS id, a.date AS date, a.title AS title, collect(d.datum)[..5] AS data_points,
       a.url AS url ORDER BY a.date""",
    },
    "Q11": {
        "question": "Coverage of the 2019 FDA warning against abrupt tapering — and whether prescriber behavior changed.",
        "cypher": f"""
MATCH (a:Article) WHERE EXISTS {{ MATCH (a)-[:REPORTS_ON]->(:Policy {{name:'FDA_TAPER_WARNING_2019'}}) }}
   OR (toLower(a.core_summary) CONTAINS 'fda' AND toLower(a.core_summary) CONTAINS 'abrupt')
{_RETURN_ARTICLE}""",
    },
    "Q12": {
        "question": "Coverage of the CDC 2022 revision and its enumerated 'misapplications' of the 2016 guideline.",
        "cypher": f"""
MATCH (a:Article) WHERE EXISTS {{ MATCH (a)-[:REPORTS_ON]->(:Policy {{name:'CDC_2022_REVISION'}}) }}
   OR toLower(a.core_summary) CONTAINS 'misappl'
   OR (a.date >= '2022-01-01' AND toLower(a.core_summary) CONTAINS 'cdc'
       AND 'GUIDELINE_CRITIQUE' IN a.tags)
{_RETURN_ARTICLE}""",
    },
    "Q13": {
        "question": "DEA/law-enforcement actions against prescribers followed by mass patient displacement, with patient counts.",
        "cypher": f"""
MATCH (a:Article) WHERE 'PATIENT_ABANDONMENT' IN a.tags AND any(kw IN
  ['dea','raid','indict','arrest','license','prosecut']
  WHERE toLower(a.core_summary) CONTAINS kw)
OPTIONAL MATCH (a)-[:CITES]->(d:DataPoint) WHERE toLower(d.datum) CONTAINS 'patient'
RETURN a.id AS id, a.date AS date, a.title AS title, collect(d.datum) AS patient_counts,
       a.url AS url ORDER BY a.date""",
    },
    "Q14": {
        "question": "PDMP / NarxCare-style risk scores leading to care denials; vendors named.",
        "cypher": f"""
MATCH (a:Article) WHERE any(kw IN ['pdmp','narxcare','risk score','bamboo health','appriss']
  WHERE toLower(a.core_summary) CONTAINS kw OR toLower(a.title) CONTAINS kw)
{_RETURN_ARTICLE}""",
    },
    "Q15": {
        "question": "Policies coverage credits with REDUCING harm (naloxone access, X-waiver removal, safe supply) and cited evidence.",
        "cypher": f"""
MATCH (a:Article) WHERE any(kw IN ['naloxone','narcan','x-waiver','x waiver','safe supply','harm reduction']
  WHERE toLower(a.core_summary) CONTAINS kw)
OPTIONAL MATCH (a)-[:CITES]->(d:DataPoint)
RETURN a.id AS id, a.date AS date, a.title AS title, collect(d.datum)[..3] AS evidence,
       a.url AS url ORDER BY a.date""",
    },

    # ---------- C. Evidence and Guideline Controversies ----------
    "Q16": {
        "question": "Critiques of CDC 2016 / VA-DoD guideline evidence base, with named researcher critics.",
        "cypher": """
MATCH (a:Article) WHERE 'GUIDELINE_CRITIQUE' IN a.tags
OPTIONAL MATCH (a)-[:MENTIONS]->(p:Person) WHERE p.role IN ['researcher','clinician']
RETURN a.id AS id, a.date AS date, a.title AS title,
       collect(DISTINCT p.name) AS named_critics, a.url AS url ORDER BY a.date""",
    },
    "Q17": {
        "question": "GRADE evidence-rating discussion — 'low-quality evidence' converted into strong mandates.",
        "cypher": f"""
MATCH (a:Article) WHERE toLower(a.core_summary) CONTAINS 'grade'
   OR toLower(a.core_summary) CONTAINS 'low-quality evidence'
   OR toLower(a.core_summary) CONTAINS 'quality of evidence'
{_RETURN_ARTICLE}""",
    },
    "Q18": {
        "question": "Articles citing Oliva et al. 2020 or similar discontinuation–overdose/suicide research, and official responses.",
        "cypher": f"""
MATCH (a:Article)
OPTIONAL MATCH (a)-[:CITES]->(d:DataPoint)
WITH a, collect(toLower(coalesce(d.datum,'') + ' ' + coalesce(d.source_in_article,''))) AS data_text
WHERE toLower(a.core_summary) CONTAINS 'oliva'
   OR any(t IN data_text WHERE t CONTAINS 'oliva')
   OR (toLower(a.core_summary) CONTAINS 'discontinuation'
       AND (toLower(a.core_summary) CONTAINS 'suicide' OR toLower(a.core_summary) CONTAINS 'overdose'))
{_RETURN_ARTICLE}""",
    },
    "Q19": {
        "question": "Guideline authors / CDC / VA officials defending the guidelines, and their counter-evidence.",
        "cypher": """
MATCH (a:Article) WHERE 'GUIDELINE_DEFENSE' IN a.tags
OPTIONAL MATCH (a)-[:MENTIONS]->(p:Person {role:'official'})
OPTIONAL MATCH (a)-[:CITES]->(q:Quote)
RETURN a.id AS id, a.date AS date, a.title AS title, collect(DISTINCT p.name) AS officials,
       collect(DISTINCT q.text)[..3] AS defense_quotes, a.url AS url ORDER BY a.date""",
    },
    "Q20": {
        "question": "Conflict-of-interest coverage on either side (pharma funding of advocacy; advocacy influence on committees).",
        "cypher": f"""
MATCH (a:Article) WHERE any(kw IN ['conflict of interest','funded by','pharma funding','prop ','kolodny','disclosure']
  WHERE toLower(a.core_summary) CONTAINS kw)
{_RETURN_ARTICLE}""",
    },
    "Q21": {
        "question": "Dependence vs. addiction (OUD) distinction — and where coverage conflates them.",
        "cypher": f"""
MATCH (a:Article) WHERE (toLower(a.core_summary) CONTAINS 'dependence'
   OR toLower(a.core_summary) CONTAINS 'addiction')
  AND ('TAPER_HARM' IN a.tags OR 'STIGMA' IN a.tags)
{_RETURN_ARTICLE}""",
    },

    # ---------- D. Veterans and the VA System ----------
    "Q22": {
        "question": "VA facilities named in connection with taper-related patient harm, with dates and counts.",
        "cypher": """
MATCH (a:Article) WHERE 'TAPER_HARM' IN a.tags AND a.va_facility <> ''
RETURN a.va_facility AS facility, count(a) AS n_articles,
       collect({id:a.id, date:a.date, title:a.title})[..10] AS articles
ORDER BY n_articles DESC""",
    },
    "Q23": {
        "question": "Veteran suicides on/near VA property ('parking-lot suicides') connected to pain-care denials.",
        "cypher": f"""
MATCH (a:Article) WHERE 'VETERAN_SUICIDE' IN a.tags AND any(kw IN
  ['parking lot','parking-lot','on va property','outside the va','va campus']
  WHERE toLower(a.core_summary) CONTAINS kw OR toLower(a.title) CONTAINS kw)
{_RETURN_ARTICLE}""",
    },
    "Q24": {
        "question": "Official VA responses when coverage linked its policies to veteran suicides (denial, review, testimony, revision).",
        "cypher": """
MATCH (a:Article) WHERE 'VETERAN_SUICIDE' IN a.tags
MATCH (a)-[:MENTIONS]->(p:Person {role:'official'})
OPTIONAL MATCH (a)-[:CITES]->(q:Quote) WHERE toLower(q.speaker) CONTAINS 'va'
RETURN a.id AS id, a.date AS date, a.title AS title, collect(DISTINCT p.name) AS officials,
       collect(DISTINCT q.text)[..3] AS response_quotes, a.url AS url ORDER BY a.date""",
    },
    "Q25": {
        "question": "Congressional hearings, GAO reports, VA OIG investigations into pain management/tapering — findings and dates.",
        "cypher": f"""
MATCH (a:Article) WHERE any(kw IN ['gao','inspector general','oig','congressional hearing','testimony','house committee','senate committee']
  WHERE toLower(a.core_summary) CONTAINS kw)
  AND ('TAPER_HARM' IN a.tags OR 'GUIDELINE_CRITIQUE' IN a.tags OR 'VETERAN_SUICIDE' IN a.tags)
{_RETURN_ARTICLE}""",
    },
    "Q26": {
        "question": "VA prescribing metrics (dashboards, facility rates) compared against veteran-reported access.",
        "cypher": f"""
MATCH (a:Article) WHERE any(kw IN ['prescribing rate','dashboard','opioid prescribing','metric']
  WHERE toLower(a.core_summary) CONTAINS kw) AND toLower(a.core_summary) CONTAINS 'va'
{_RETURN_ARTICLE}""",
    },
    "Q27": {
        "question": "Veteran service organizations (VFW, DAV, Wounded Warrior) in coverage, and their positions.",
        "cypher": """
MATCH (a:Article)-[m:MENTIONS]->(p:Person)
WHERE toLower(p.affiliation) CONTAINS 'vfw' OR toLower(p.affiliation) CONTAINS 'dav'
   OR toLower(p.affiliation) CONTAINS 'veteran' OR toLower(p.affiliation) CONTAINS 'wounded warrior'
RETURN a.id AS id, a.date AS date, a.title AS title, p.name AS person,
       p.affiliation AS org, m.position_taken AS position ORDER BY a.date""",
    },

    # ---------- E. Kratom and 7-OH Policy ----------
    "Q28": {
        "question": "Reported reasons for kratom use (pain, withdrawal, replacement, energy/mood) — which dominate by article count.",
        "cypher": """
MATCH (a:Article)-[:MENTIONS]->(:Substance {name:'Kratom'})
WITH a, [reason IN [
  ['pain', 'pain'], ['withdrawal', 'withdraw'], ['replacement', 'instead of'],
  ['energy_mood', 'energy'], ['energy_mood', 'mood']
] WHERE toLower(a.core_summary) CONTAINS reason[1] | reason[0]] AS reasons
UNWIND (CASE WHEN size(reasons)=0 THEN ['unspecified'] ELSE reasons END) AS reason
RETURN reason, count(DISTINCT a) AS n_articles ORDER BY n_articles DESC""",
    },
    "Q29": {
        "question": "Kratom ban states vs. KCPA states — coverage and reported outcomes for each approach.",
        "cypher": """
MATCH (a:Article) WHERE 'KRATOM_POLICY' IN a.tags
WITH a, CASE
  WHEN EXISTS {MATCH (a)-[:REPORTS_ON]->(:Policy {name:'STATE_KRATOM_BAN'})}
       OR toLower(a.core_summary) CONTAINS 'ban' THEN 'ban'
  WHEN EXISTS {MATCH (a)-[:REPORTS_ON]->(:Policy {name:'STATE_KCPA'})}
       OR toLower(a.core_summary) CONTAINS 'consumer protection' THEN 'kcpa'
  ELSE 'other' END AS approach
RETURN approach, a.state AS state, count(a) AS n_articles,
       collect({id:a.id, date:a.date, title:a.title})[..5] AS sample
ORDER BY approach, n_articles DESC""",
    },
    "Q30": {
        "question": "The 2016 DEA emergency-scheduling attempt and withdrawal — objecting scientists and members of Congress.",
        "cypher": """
MATCH (a:Article) WHERE 'KRATOM_POLICY' IN a.tags
  AND a.date >= '2016-08-01' AND a.date <= '2017-06-30'
  AND (toLower(a.core_summary) CONTAINS 'dea' OR toLower(a.core_summary) CONTAINS 'schedul')
OPTIONAL MATCH (a)-[:MENTIONS]->(p:Person)
RETURN a.id AS id, a.date AS date, a.title AS title,
       collect(DISTINCT p.name) AS people, a.url AS url ORDER BY a.date""",
    },
    "Q31": {
        "question": "Leaf kratom vs. concentrated 7-OH distinction — and first appearance of '7-OH' as a distinct term.",
        "cypher": """
MATCH (a:Article)-[:MENTIONS]->(:Substance {name:'7-OH'})
RETURN a.date AS date, a.id AS id, a.title AS title,
       EXISTS {MATCH (a)-[:MENTIONS]->(:Substance {name:'Kratom'})} AS also_mentions_kratom,
       a.url AS url
ORDER BY a.date ASC""",
    },
    "Q32": {
        "question": "FDA's 2025 7-OH scheduling push — industry factions for/against; AKA vs. 7-OH sellers.",
        "cypher": """
MATCH (a:Article)-[:MENTIONS]->(:Substance {name:'7-OH'})
WHERE a.date >= '2025-01-01'
OPTIONAL MATCH (a)-[m:MENTIONS]->(p:Person) WHERE p.role IN ['industry','advocate','official']
RETURN a.id AS id, a.date AS date, a.title AS title, a.stance_signal AS stance,
       collect(DISTINCT {name:p.name, role:p.role, position:m.position_taken}) AS actors,
       a.url AS url ORDER BY a.date""",
    },
    "Q33": {
        "question": "Deaths attributed to kratom/7-OH — underlying toxicology (sole vs. polydrug) and who disputes attribution.",
        "cypher": """
MATCH (a:Article)-[:MENTIONS]->(s:Substance) WHERE s.name IN ['Kratom','7-OH']
  AND (toLower(a.core_summary) CONTAINS 'death' OR toLower(a.core_summary) CONTAINS 'died'
       OR toLower(a.core_summary) CONTAINS 'fatal')
OPTIONAL MATCH (a)-[:CITES]->(d:DataPoint)
  WHERE toLower(d.datum) CONTAINS 'toxicolog' OR toLower(d.datum) CONTAINS 'polydrug'
     OR toLower(d.datum) CONTAINS 'medical examiner'
RETURN a.id AS id, a.date AS date, a.title AS title, s.name AS substance,
       collect(d.datum) AS toxicology, ('DATA_LIMITATION' IN a.tags) AS attribution_disputed,
       a.url AS url ORDER BY a.date""",
    },

    # ---------- F. Data and Surveillance Limitations ----------
    "Q34": {
        "question": "FAERS limitations (voluntary reporting, no denominator, duplicates) in coverage justifying policy.",
        "cypher": f"""
MATCH (a:Article) WHERE 'DATA_LIMITATION' IN a.tags
  AND (toLower(a.core_summary) CONTAINS 'faers' OR toLower(a.core_summary) CONTAINS 'adverse event')
{_RETURN_ARTICLE}""",
    },
    "Q35": {
        "question": "Poison-control (NPDS) data in kratom/7-OH coverage — is 'call volume is not incidence' noted?",
        "cypher": f"""
MATCH (a:Article) WHERE (toLower(a.core_summary) CONTAINS 'poison control'
   OR toLower(a.core_summary) CONTAINS 'npds')
  AND EXISTS {{ MATCH (a)-[:MENTIONS]->(s:Substance) WHERE s.name IN ['Kratom','7-OH'] }}
RETURN a.id AS id, a.date AS date, a.title AS title,
       ('DATA_LIMITATION' IN a.tags) AS limitation_noted, a.url AS url ORDER BY a.date""",
    },
    "Q36": {
        "question": "Postmortem toxicology practices — polydrug reported as single-substance, metabolite misID, ME inconsistency.",
        "cypher": f"""
MATCH (a:Article) WHERE 'DATA_LIMITATION' IN a.tags AND any(kw IN
  ['postmortem','post-mortem','toxicolog','medical examiner','coroner','autopsy']
  WHERE toLower(a.core_summary) CONTAINS kw)
{_RETURN_ARTICLE}""",
    },
    "Q37": {
        "question": "Missing denominators — user-population estimates or stable-patient counts absent from risk claims.",
        "cypher": f"""
MATCH (a:Article) WHERE 'DATA_LIMITATION' IN a.tags AND any(kw IN
  ['denominator','per capita','how many people use','user population','base rate']
  WHERE toLower(a.core_summary) CONTAINS kw)
{_RETURN_ARTICLE}""",
    },
    "Q38": {
        "question": "Gaming/artifacts in prescribing metrics (e.g., improving dashboards by discharging patients).",
        "cypher": f"""
MATCH (a:Article) WHERE any(kw IN ['metric','dashboard','quota','target']
  WHERE toLower(a.core_summary) CONTAINS kw)
  AND ('PATIENT_ABANDONMENT' IN a.tags OR 'DATA_LIMITATION' IN a.tags)
{_RETURN_ARTICLE}""",
    },

    # ---------- G. Cross-Cutting Analytical Questions ----------
    "Q39": {
        "question": "Whose perspective is absent — quoted-source role mix per tag (officials vs. patients vs. researchers).",
        "cypher": """
MATCH (a:Article)-[:MENTIONS]->(p:Person)
UNWIND a.tags AS tag
WITH tag, p.role AS role, count(DISTINCT p) AS n
WHERE NOT tag STARTS WITH 'SCOPE'
RETURN tag, collect({role:role, quoted_people:n}) AS role_mix
ORDER BY tag""",
    },
    "Q40": {
        "question": "Coverage volume by month per tag — read against the fixed policy-event timeline for spike analysis.",
        "cypher": """
MATCH (a:Article) WHERE a.date <> ''
UNWIND a.tags AS tag
WITH tag, substring(a.date, 0, 7) AS month, count(*) AS n
WHERE NOT tag STARTS WITH 'SCOPE'
RETURN tag, month, n ORDER BY tag, month""",
    },
    "Q41": {
        "question": "Contradictions between official statements and patient accounts within the same article.",
        "cypher": """
MATCH (a:Article)-[:MENTIONS]->(off:Person {role:'official'})
MATCH (a)-[:MENTIONS]->(pat:Person) WHERE pat.role IN ['patient','family']
WHERE 'POLICY_TENSION' IN a.tags OR a.stance_signal = 'mixed'
OPTIONAL MATCH (a)-[:CITES]->(q:Quote)
RETURN a.id AS id, a.date AS date, a.title AS title,
       collect(DISTINCT off.name) AS officials, collect(DISTINCT pat.name) AS patients,
       collect(DISTINCT q.text)[..4] AS quotes, a.url AS url ORDER BY a.date""",
    },
    "Q42": {
        "question": "Narratives in national but not local coverage (and vice versa) — tag distribution by scope.",
        "cypher": """
MATCH (a:Article)
UNWIND [t IN a.tags WHERE NOT t STARTS WITH 'SCOPE'] AS tag
WITH tag,
     sum(CASE WHEN 'SCOPE_LOCAL'    IN a.tags THEN 1 ELSE 0 END) AS local_n,
     sum(CASE WHEN 'SCOPE_NATIONAL' IN a.tags THEN 1 ELSE 0 END) AS national_n
RETURN tag, local_n, national_n,
       CASE WHEN national_n = 0 THEN 'local_only'
            WHEN local_n = 0 THEN 'national_only'
            ELSE round(10.0 * local_n / national_n) / 10.0 END AS local_to_national_ratio
ORDER BY tag""",
    },
}


def run_query(client: "Neo4jClient", query_id: str, **params) -> list[dict]:  # noqa: F821
    """Execute one framework query through an initialized Neo4jClient."""
    entry = QUERIES[query_id]
    with client._driver.session(database=client.database) as session:  # noqa: SLF001
        return [dict(rec) for rec in session.run(entry["cypher"], **params)]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run framework exploratory queries")
    parser.add_argument("query_id", nargs="?", help="e.g. Q22")
    parser.add_argument("--list", action="store_true", help="List all 42 questions")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    if args.list or not args.query_id:
        for qid, entry in QUERIES.items():
            print(f"{qid:>4}: {entry['question']}")
    else:
        from llm_client import load_config
        from neo4j_client import Neo4jClient

        cfg = load_config(args.config)
        with Neo4jClient(cfg) as neo:
            for row in run_query(neo, args.query_id.upper()):
                print(row)
