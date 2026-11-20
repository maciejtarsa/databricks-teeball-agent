"""Measure retrieval on its own: does the selected context contain the rule/section each question is about?

No LLM answer or judge: only the question embedding is computed, so it is fast and cheap.
Hit rules:
- question has rule numbers (e.g. "17.8(a)" -> "17.8"): hit if a retrieved chunk has that rule number,
  or the parent rule ("17" for "17.8") or a child ("17.8" for "17")
- Manual-only question (no rule numbers): hit if a retrieved heading shares most words with the topic

Args: catalog schema embedding_endpoint evals_json modes label src_dir
Appends to {catalog}.{schema}.retrieval_runs.
"""
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

from pyspark.sql import SparkSession

sys.path.insert(0, sys.argv[-1])  # src/jobs dir (serverless tasks have no __file__)
from rag_sql import function_body  # noqa: E402

catalog, schema, embedding_endpoint, evals_json, modes, label = sys.argv[1:7]
spark = SparkSession.builder.getOrCreate()
AREAS = {"2": "rules 1-9", "3": "rules 10-16", "4": "rule 17", "5": "rules 18-22 + manual"}


def base_rule(r):
    m = re.match(r"\d+(\.\d+)?", r.strip())
    return m.group() if m else None


def words(s):
    return {w for w in re.findall(r"[a-z]+", (s or "").lower()) if len(w) > 2}


def is_hit(q, chunk):
    rules = {b for b in (base_rule(r) for r in q.get("rules", [])) if b}
    got = chunk.get("rule_no")
    if rules:
        return bool(got) and any(got == r or r.split(".")[0] == got or got.split(".")[0] == r for r in rules)
    topic = words(q["topic"].split(":")[0]) | words(q["topic"])
    return len(topic & words(chunk.get("heading"))) >= max(1, len(topic) // 2)


with open(evals_json) as f:
    evals = {e["id"]: e for e in json.load(f)}
spark.createDataFrame([(i, e["question"]) for i, e in evals.items()], "id INT, question STRING") \
    .createOrReplaceTempView("evals")

run_at = datetime.now(timezone.utc)
out = []
for mode in [m.strip() for m in modes.split("|") if m.strip()]:
    spark.sql(f"""
    CREATE OR REPLACE TEMPORARY FUNCTION retrieve_variant(question STRING) RETURNS STRING
    RETURN {function_body(catalog, schema, "unused", embedding_endpoint, "", mode, output="retrieval")}
    """)
    stats = defaultdict(Counter)
    for r in spark.sql("SELECT id, retrieve_variant(question) AS retrieved FROM evals").collect():
        q, chunks = evals[r.id], json.loads(r.retrieved or "[]")
        rank = next((i for i, c in enumerate(chunks, start=1) if is_hit(q, c)), None)
        area = AREAS.get(str(r.id)[0], "other")
        for key in (area, "ALL"):
            stats[key]["n"] += 1
            stats[key]["hit"] += rank is not None
            stats[key]["hit@3"] += rank is not None and rank <= 3
        out.append((run_at, label, mode, r.id, area, rank is not None, rank, r.retrieved))
    for key in sorted(stats):
        s = stats[key]
        print(f"{label} | {mode} | {key:<22} n={s['n']:<3} recall={s['hit'] / s['n']:.0%}  hit@3={s['hit@3'] / s['n']:.0%}")

spark.createDataFrame(out, "run_at TIMESTAMP, label STRING, mode STRING, id INT, area STRING, hit BOOLEAN, "
                           "first_hit_rank INT, retrieved STRING") \
    .write.mode("append").saveAsTable(f"{catalog}.{schema}.retrieval_runs")
