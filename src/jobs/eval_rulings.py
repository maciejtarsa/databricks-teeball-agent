"""Run logged rulings through ask_rulebook and score each answer with an LLM judge.

Args: catalog schema judge_endpoint evals_json
Writes {catalog}.{schema}.eval_results (one row per question per run).
"""
import json
import sys
from datetime import datetime, timezone

from pyspark.sql import SparkSession

catalog, schema, judge_endpoint, evals_json = sys.argv[1:5]
spark = SparkSession.builder.getOrCreate()
results_tbl = f"{catalog}.{schema}.eval_results"

JUDGE_PROMPT = """You grade a teeball rules assistant. Compare the ANSWER with the EXPECTED ruling written by an experienced umpire.
Grade:
- CORRECT: same ruling and covers the key conditions in EXPECTED.
- PARTIAL: ruling right but misses or muddles an important condition, or hedges where EXPECTED is clear.
- WRONG: different ruling, or a key fact contradicts EXPECTED.
Also check citations: CITES_RULE = YES if the answer cites at least one rule number that appears in EXPECTED (or the same rule), else NO.
Reply in exactly two lines:
VERDICT: CORRECT|PARTIAL|WRONG; CITES_RULE: YES|NO
REASON: <one sentence>"""

with open(evals_json) as f:
    evals = json.load(f)

spark.createDataFrame(evals, "id INT, question STRING, expected STRING").createOrReplaceTempView("evals")
rows = spark.sql(f"""
WITH answered AS (
  SELECT id, question, expected, {catalog}.{schema}.ask_rulebook(question) AS answer FROM evals
)
SELECT id, question, expected, answer,
       ai_query('{judge_endpoint}',
         concat('{JUDGE_PROMPT.replace("'", "''")}', '\\n\\nQUESTION: ', question,
                '\\n\\nEXPECTED: ', expected, '\\n\\nANSWER: ', answer),
         modelParameters => named_struct('temperature', 0.0)) AS judgement
FROM answered
""").collect()

run_at = datetime.now(timezone.utc)
out = []
for r in sorted(rows, key=lambda r: r.id):
    j = r.judgement or ""
    verdict = next((v for v in ("CORRECT", "PARTIAL", "WRONG") if f"VERDICT: {v}" in j), "UNKNOWN")
    cites = "CITES_RULE: YES" in j
    out.append((run_at, r.id, r.question, r.expected, r.answer, verdict, cites, j))
    print(f"#{r.id:>2} {verdict:<8} cites_rule={cites}  {r.question[:70]}")

scores = {v: sum(1 for o in out if o[5] == v) for v in ("CORRECT", "PARTIAL", "WRONG", "UNKNOWN")}
print(f"\n{scores}  cites_rule={sum(o[6] for o in out)}/{len(out)}")

spark.createDataFrame(out, "run_at TIMESTAMP, id INT, question STRING, expected STRING, answer STRING, "
                           "verdict STRING, cites_rule BOOLEAN, judgement STRING") \
    .write.mode("append").saveAsTable(results_tbl)
