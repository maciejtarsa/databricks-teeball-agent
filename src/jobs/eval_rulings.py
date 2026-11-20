"""Score answer variants (model x retrieval mode) against the logged rulings with an LLM judge.

Each variant is built from the same SQL template as ask_rulebook (rag_sql.py) as a temporary function,
so the deployed agent is untouched.

Args: catalog schema embedding_endpoint prompt_file evals_json judge_endpoint models modes label src_dir
  models / modes: "|"-separated (CLI --params splits on commas), e.g. "databricks-gpt-oss-120b|databricks-qwen35-122b-a10b" / "vector|hybrid"
Appends to {catalog}.{schema}.eval_runs.
"""
import json
import sys
from datetime import datetime, timezone

from pyspark.sql import SparkSession

sys.path.insert(0, sys.argv[-1])  # src/jobs dir (serverless tasks have no __file__)
from rag_sql import function_body, sql_str  # noqa: E402

catalog, schema, embedding_endpoint, prompt_file, evals_json, judge, models, modes, label = sys.argv[1:10]
spark = SparkSession.builder.getOrCreate()
runs_tbl = f"{catalog}.{schema}.eval_runs"

JUDGE_PROMPT = """You grade a teeball rules assistant. Compare the ANSWER with the EXPECTED ruling written by an experienced umpire.
Grade:
- CORRECT: same ruling and covers the key conditions in EXPECTED.
- PARTIAL: ruling right but misses or muddles an important condition, or hedges where EXPECTED is clear.
- WRONG: different ruling, or a key fact contradicts EXPECTED.
Also check citations: CITES_RULE = YES if the answer cites at least one rule number that appears in EXPECTED (or the same rule), else NO.
Reply in exactly two lines:
VERDICT: CORRECT|PARTIAL|WRONG; CITES_RULE: YES|NO
REASON: <one sentence>"""

with open(prompt_file) as f:
    prompt = f.read()
with open(evals_json) as f:
    evals = json.load(f)
spark.createDataFrame([(e["id"], e["question"], e["expected"]) for e in evals], "id INT, question STRING, expected STRING").createOrReplaceTempView("evals")

run_at = datetime.now(timezone.utc)
out = []
for model in [m.strip() for m in models.split("|") if m.strip()]:
    for mode in [m.strip() for m in modes.split("|") if m.strip()]:
        spark.sql(f"""
        CREATE OR REPLACE TEMPORARY FUNCTION ask_variant(question STRING) RETURNS STRING
        RETURN {function_body(catalog, schema, model, embedding_endpoint, prompt, mode)}
        """)
        rows = spark.sql(f"""
        WITH answered AS (SELECT id, question, expected, ask_variant(question) AS answer FROM evals)
        SELECT id, question, expected, answer,
               ai_query({sql_str(judge)},
                 concat({sql_str(JUDGE_PROMPT)}, '\\n\\nQUESTION: ', question,
                        '\\n\\nEXPECTED: ', expected, '\\n\\nANSWER: ', coalesce(answer, '')),
                 modelParameters => named_struct('temperature', 0.0)) AS judgement
        FROM answered
        """).collect()
        for r in sorted(rows, key=lambda r: r.id):
            j = r.judgement or ""
            verdict = next((v for v in ("CORRECT", "PARTIAL", "WRONG") if f"VERDICT: {v}" in j), "UNKNOWN")
            if not (r.answer or "").strip():
                # The judge happily grades empty answers as CORRECT, so never trust it on those
                verdict, j = "WRONG", "EMPTY ANSWER (not judged)"
            out.append((run_at, label, model, mode, judge, r.id, r.question, r.expected, r.answer,
                        verdict, "CITES_RULE: YES" in j, j))
        variant = [o for o in out if o[2] == model and o[3] == mode]
        counts = {v: sum(1 for o in variant if o[9] == v) for v in ("CORRECT", "PARTIAL", "WRONG", "UNKNOWN")}
        print(f"{label} | {model} | {mode} | {counts} | cites_rule={sum(o[10] for o in variant)}/{len(variant)} | "
              f"by id: {' '.join(f'{o[5]}:{o[9][0]}' for o in variant)}")

spark.createDataFrame(out, "run_at TIMESTAMP, label STRING, model STRING, mode STRING, judge STRING, id INT, "
                           "question STRING, expected STRING, answer STRING, verdict STRING, cites_rule BOOLEAN, "
                           "judgement STRING") \
    .write.mode("append").saveAsTable(runs_tbl)
