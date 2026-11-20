"""Generate an evaluation set: a "teacher" LLM writes question/expected-answer pairs per rule topic.

The teacher sees the FULL Rule Book plus all text of the target rule (both documents), i.e. far more context
than the RAG "student" it will be used to grade.

Quality gates (synthetic questions are not trustworthy on their own):
1. dedupe: drop questions whose embedding is near-identical to an earlier one
2. verify: a different model (checker) with the same full context marks each pair VALID/INVALID

Args: catalog schema teacher_endpoint prompt_file questions_per_topic limit out_json checker_endpoint embedding_endpoint
  limit: max topics (0 = all); use a small number for a pilot run.
Writes {catalog}.{schema}.eval_questions (all, with status) and out_json (only kept questions; a volume path).
"""
import json
import os
import re
import sys

from pyspark.sql import SparkSession

DUPLICATE_THRESHOLD = 0.93
CHECK_PROMPT = """You review questions for a teeball umpire test, using ONLY the documents below (TBAWA Rule Book Ed 8
wins over the Annotated Manual). A pair is VALID only if: the situation is coherent and physically possible, it is
answerable from the documents, and the expected answer is correct, cites the right rules, and is not baseball-only.
Reply in exactly two lines:
VERDICT: VALID|INVALID
REASON: <one sentence>"""

catalog, schema, teacher, prompt_file, per_topic, limit, out_json, checker, embedder = sys.argv[1:10]
per_topic, limit = int(per_topic), int(limit)
spark = SparkSession.builder.getOrCreate()
chunks_tbl = f"{catalog}.{schema}.rule_chunks"

with open(prompt_file) as f:
    prompt = f.read().replace("{n}", str(per_topic))

rule_book = "\n\n".join(r.content for r in spark.sql(f"""
    SELECT content FROM {chunks_tbl} WHERE doc_type = 'rulebook' ORDER BY page_start, rule_no""").collect())

# Topics: every rule number (text from both documents), plus substantial unnumbered Manual sections
topics = spark.sql(f"""
    SELECT concat('rule ', rule_no) AS topic, concat_ws('\\n\\n', collect_list(content)) AS text
    FROM {chunks_tbl} WHERE rule_no IS NOT NULL GROUP BY rule_no
    UNION ALL
    SELECT concat('section ', heading, ' p', page_start) AS topic, content AS text
    FROM {chunks_tbl} WHERE rule_no IS NULL AND doc_type = 'manual' AND length(content) > 500
    ORDER BY topic
""").collect()
if limit:
    topics = topics[:limit]
print(f"{len(topics)} topics, {per_topic} question(s) each")

spark.createDataFrame(
    [(t.topic, f"{prompt}\n\nFULL RULE BOOK:\n{rule_book}\n\nTARGET SECTION ({t.topic}):\n{t.text}") for t in topics],
    "topic STRING, request STRING").createOrReplaceTempView("gen_requests")
responses = spark.sql(f"""
    SELECT topic, ai_query('{teacher}', request,
                           modelParameters => named_struct('temperature', 0.7, 'max_tokens', 8000)) AS response
    FROM gen_requests""").collect()

context = {t.topic: f"FULL RULE BOOK:\n{rule_book}\n\nTARGET SECTION ({t.topic}):\n{t.text}" for t in topics}
rows, failed = [], []
for r in responses:
    text = re.sub(r"^```(json)?|```$", "", (r.response or "").strip(), flags=re.M).strip()
    try:
        items = json.loads(text[text.index("["): text.rindex("]") + 1])
    except ValueError:
        failed.append(r.topic)
        continue
    for q in items:
        if q.get("question") and q.get("expected"):
            rows.append({"topic": r.topic, "question": q["question"].strip(), "expected": q["expected"].strip(),
                         "rules": [str(x) for x in q.get("rules", [])], "difficulty": q.get("difficulty", "")})
for i, row in enumerate(rows, start=1):
    row["id"] = 1000 + i  # generated ids start at 1001; hand-written rulings are 1-12
    row["status"], row["reason"] = "kept", ""
print(f"{len(rows)} questions; {len(failed)} topic(s) failed to parse: {failed}")

# 1. Dedupe near-identical questions (cosine on embeddings)
spark.createDataFrame([(r["id"], r["question"]) for r in rows], "id INT, question STRING").createOrReplaceTempView("gen_q")
emb = {r.id: r.v for r in spark.sql(f"SELECT id, ai_query('{embedder}', question) AS v FROM gen_q").collect()}


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    return dot / ((sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5))


kept_vecs = []
for row in rows:
    v = emb[row["id"]]
    if any(cosine(v, k) > DUPLICATE_THRESHOLD for k in kept_vecs):
        row["status"], row["reason"] = "duplicate", f"cosine > {DUPLICATE_THRESHOLD} with an earlier question"
    else:
        kept_vecs.append(v)

# 2. Independent check by a different model, same context as the teacher
spark.createDataFrame(
    [(r["id"], f"{CHECK_PROMPT}\n\n{context[r['topic']]}\n\nQUESTION: {r['question']}\n\nEXPECTED ANSWER: {r['expected']}")
     for r in rows if r["status"] == "kept"], "id INT, request STRING").createOrReplaceTempView("check_requests")
checks = {r.id: r.verdict or "" for r in spark.sql(f"""
    SELECT id, ai_query('{checker}', request, modelParameters => named_struct('temperature', 0.0)) AS verdict
    FROM check_requests""").collect()}
for row in rows:
    if row["id"] in checks and "VERDICT: VALID" not in checks[row["id"]]:
        row["status"], row["reason"] = "invalid", checks[row["id"]].strip()[:500]
counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("kept", "duplicate", "invalid")}
print(f"quality gates: {counts}")

spark.createDataFrame(rows, "id INT, topic STRING, question STRING, expected STRING, rules ARRAY<STRING>, "
                            "difficulty STRING, status STRING, reason STRING") \
    .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{catalog}.{schema}.eval_questions")
os.makedirs(os.path.dirname(out_json), exist_ok=True)
with open(out_json, "w") as f:
    json.dump([{k: row[k] for k in ("id", "topic", "question", "expected", "rules", "difficulty")}
               for row in rows if row["status"] == "kept"],
              f, indent=2, ensure_ascii=False)
print(f"wrote {out_json}")
