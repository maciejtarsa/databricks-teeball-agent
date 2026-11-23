"""Ask the deployed Genie agent the logged rulings through the Genie Conversation API and score what users see.

eval_rulings tests ask_rulebook directly; this tests the agent end to end: did Genie call ask_rulebook
(or write its own query), and is the answer it shows right?

Args: catalog schema space_id evals_json judge_endpoint label id_prefix src_dir
Appends to {catalog}.{schema}.genie_runs.
"""
import json
import sys
import time
from datetime import datetime, timezone

from databricks.sdk import WorkspaceClient
from pyspark.sql import SparkSession

sys.path.insert(0, sys.argv[-1])  # src/jobs dir (serverless tasks have no __file__)
from eval_judge import JUDGE_PROMPT  # noqa: E402
from rag_sql import sql_str  # noqa: E402

catalog, schema, space_id, evals_json, judge, label, id_prefix = sys.argv[1:8]
spark = SparkSession.builder.getOrCreate()
w = WorkspaceClient()

with open(evals_json) as f:
    evals = json.load(f)
if id_prefix != "all":
    evals = [e for e in evals if str(e["id"]).startswith(tuple(id_prefix.split("|")))]

rows = []
for e in evals:
    queries, answer, status = [], "", "FAILED"
    for attempt in range(3):  # Genie API is rate limited; back off and retry
        try:
            msg = w.genie.start_conversation_and_wait(space_id, e["question"])
            status = msg.status.value if msg.status else "UNKNOWN"
            for a in msg.attachments or []:
                if a.query and a.query.query:
                    queries.append(a.query.query)
                if a.text and a.text.content:
                    answer += a.text.content
            break
        except Exception as ex:
            print(f"id {e['id']} attempt {attempt + 1}: {ex}")
            time.sleep(30)
    used_function = any("ask_rulebook" in q for q in queries)
    print(f"id {e['id']}: {status} | ask_rulebook={used_function} | {len(answer)} chars")
    rows.append((e["id"], e["question"], e["expected"], answer, status, used_function, "\n---\n".join(queries)))
    time.sleep(5)

spark.createDataFrame(rows, "id INT, question STRING, expected STRING, answer STRING, status STRING, "
                            "used_function BOOLEAN, queries STRING").createOrReplaceTempView("genie_answers")
judged = spark.sql(f"""
SELECT *, ai_query({sql_str(judge)},
         concat({sql_str(JUDGE_PROMPT)}, '\\n\\nQUESTION: ', question,
                '\\n\\nEXPECTED: ', expected, '\\n\\nANSWER: ', coalesce(answer, '')),
         modelParameters => named_struct('temperature', 0.0)) AS judgement
FROM genie_answers
""").collect()

run_at = datetime.now(timezone.utc)
out = []
for r in sorted(judged, key=lambda r: r.id):
    j = r.judgement or ""
    verdict = next((v for v in ("CORRECT", "PARTIAL", "WRONG") if f"VERDICT: {v}" in j), "UNKNOWN")
    if not (r.answer or "").strip():
        verdict, j = "WRONG", "EMPTY ANSWER (not judged)"
    out.append((run_at, label, judge, r.id, r.question, r.expected, r.answer, r.status, r.used_function, r.queries,
                verdict, "CITES_RULE: YES" in j, j))

counts = {v: sum(1 for o in out if o[10] == v) for v in ("CORRECT", "PARTIAL", "WRONG", "UNKNOWN")}
print(f"{label} | {counts} | ask_rulebook={sum(o[8] for o in out)}/{len(out)} | "
      f"cites_rule={sum(o[11] for o in out)}/{len(out)} | by id: {' '.join(f'{o[3]}:{o[10][0]}' for o in out)}")
spark.createDataFrame(out, "run_at TIMESTAMP, label STRING, judge STRING, id INT, question STRING, expected STRING, "
                           "answer STRING, status STRING, used_function BOOLEAN, queries STRING, verdict STRING, "
                           "cites_rule BOOLEAN, judgement STRING") \
    .write.mode("append").saveAsTable(f"{catalog}.{schema}.genie_runs")
