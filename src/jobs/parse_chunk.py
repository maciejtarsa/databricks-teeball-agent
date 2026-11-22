"""Parse rulebook PDFs in the volume with ai_parse_document and split them into rule-level chunks.

Args: catalog schema volume_dir documents_json embedding_endpoint
- parsed_docs: one row per PDF (only new/changed files are re-parsed)
- rule_chunks: one row per rule section, merged by chunk_id, with an embedding per chunk (only new chunks are embedded)
- rule_terms: IDF per word (keyword weights for hybrid retrieval)
"""
import hashlib
import json
import re
import sys

from pyspark.sql import SparkSession

catalog, schema, volume_dir, documents_json, embedding_endpoint = sys.argv[1:6]
spark = SparkSession.builder.getOrCreate()
parsed_tbl = f"{catalog}.{schema}.parsed_docs"
chunks_tbl = f"{catalog}.{schema}.rule_chunks"

MAX_CHARS = 2500
MIN_CHARS = 300  # unnumbered fragments smaller than this merge into the previous chunk of the same section
RULE_HEADER = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2})?)\s+(\S.*)$")
SKIP_TYPES = {"page_header", "page_footer", "page_number"}
# Sub-headings inside a rule (manual scenarios/outcomes, lettered clauses) stay in the rule's chunk
INLINE_HEADER = re.compile(r"^(\(|SCENARIO|OUTCOME|NOTE|SPECIAL NOTE|CONCLUSION|EXAMPLE)|:$", re.IGNORECASE)


def is_inline_header(text, in_rule):
    """Scenario/outcome/note sub-headings always stay inline; short ALL-CAPS ones only inside a numbered rule."""
    return bool(INLINE_HEADER.search(text)) or (in_rule and text.isupper() and len(text) < 60)


def is_sentence(text):
    """Parser sometimes tags emphasised sentences as headings ("Do not get involved in petty squabbling...")."""
    return len(text) > 45 or text.rstrip().endswith((".", "!", ","))


def section_of(chunk):
    return (chunk["part"] or "").split(" > ")[0]


with open(documents_json) as f:
    doc_meta = {k: v for k, v in json.load(f).items() if not k.startswith("_")}

# 1. Parse new or changed PDFs
spark.sql(f"""
CREATE TABLE IF NOT EXISTS {parsed_tbl} (
  path STRING, file_name STRING, modification_time TIMESTAMP, parsed VARIANT)
""")
spark.sql(f"""
CREATE OR REPLACE TEMP VIEW new_files AS
SELECT f.path, f.modificationTime AS modification_time, f.content
FROM read_files('{volume_dir}', format => 'binaryFile', pathGlobFilter => '*.pdf') f
LEFT ANTI JOIN {parsed_tbl} p ON p.path = f.path AND p.modification_time = f.modificationTime
""")
n_new = spark.sql("SELECT count(*) FROM new_files").first()[0]
print(f"{n_new} new/changed PDF(s) to parse")
if n_new:
    spark.sql(f"""
    MERGE INTO {parsed_tbl} t
    USING (SELECT path, regexp_extract(path, '[^/]+$', 0) AS file_name, modification_time,
                  ai_parse_document(content) AS parsed
           FROM new_files) s
    ON t.path = s.path
    WHEN MATCHED THEN UPDATE SET *
    WHEN NOT MATCHED THEN INSERT *
    """)
# Drop PDFs removed from the volume
spark.sql(f"""
DELETE FROM {parsed_tbl}
WHERE path NOT IN (SELECT path FROM read_files('{volume_dir}', format => 'binaryFile', pathGlobFilter => '*.pdf'))
""")

# 2. Chunk by rule section headers
elements = spark.sql(f"""
SELECT file_name, el.type AS type, el.content AS content, el.bbox[0].page_id AS page_id, pos
FROM {parsed_tbl},
LATERAL posexplode(variant_get(parsed, '$.document.elements',
  'ARRAY<STRUCT<type STRING, content STRING, bbox ARRAY<STRUCT<page_id INT>>>>')) AS (pos, el)
ORDER BY file_name, pos
""").collect()


def new_chunk(file_name, page, rule_no=None, heading=None, part=None):
    return {"file_name": file_name, "rule_no": rule_no, "heading": heading, "part": part,
            "page_start": page, "page_end": page, "body": []}


chunks, cur, section = [], None, None
for el in elements:
    if el.type in SKIP_TYPES or not (el.content or "").strip():
        continue
    page = (el.page_id or 0) + 1  # PDF page number, 1-based
    text = el.content.strip()
    if cur is None or cur["file_name"] != el.file_name:
        if cur:
            chunks.append(cur)
        cur = new_chunk(el.file_name, page)
    m = RULE_HEADER.match(text) if el.type in ("section_header", "title") else None
    if m:
        chunks.append(cur)
        cur = new_chunk(el.file_name, page, m.group(1), m.group(2), cur["part"])
        section = None
    elif (el.type in ("section_header", "title") and not is_inline_header(text, bool(cur["rule_no"]))
          and not is_sentence(text)):
        title = " ".join(text.split())
        chunks.append(cur)
        if title.isupper() and section:
            # ALL-CAPS sub-heading of an unnumbered section, e.g. "Signals > SAFE"
            cur = new_chunk(el.file_name, page, part=f"{section} > {title}")
        else:
            section = title
            cur = new_chunk(el.file_name, page, part=title)
        cur["body"].append(title)
    else:
        if el.type == "figure":
            text = f"[Figure] {text}"
        cur["body"].append(text)
        cur["page_end"] = page
if cur:
    chunks.append(cur)

# Merge small unnumbered fragments into the previous chunk of the same section
merged = []
for c in chunks:
    prev = merged[-1] if merged else None
    if (prev and not c["rule_no"] and not prev["rule_no"] and prev["file_name"] == c["file_name"]
            and section_of(prev) == section_of(c) and len("\n".join(c["body"])) < MIN_CHARS):
        prev["body"] += c["body"]
        prev["page_end"] = max(prev["page_end"], c["page_end"])
        prev["part"] = section_of(prev) or prev["part"]
    else:
        merged.append(c)
chunks = merged

# Unnumbered sections (e.g. Manual "Obstruction") inherit the rule number of the
# highest-authority document's section with the same heading (Rule Book "20 Obstruction")
# By-laws have their own numbering (By-Law 17 Safety Bases != Rule 17 Running), so they're left out
rank = lambda c: doc_meta.get(c["file_name"], {}).get("authority_rank", 99)
is_bylaw = lambda c: doc_meta.get(c["file_name"], {}).get("doc_type") == "bylaws"
heading_rule = {}
for c in sorted((c for c in chunks if c["rule_no"] and not is_bylaw(c)), key=rank, reverse=True):
    heading_rule[c["heading"].strip().lower()] = c["rule_no"]
for c in chunks:
    if not c["rule_no"] and not is_bylaw(c) and c["part"] and c["part"].strip().lower() in heading_rule:
        c["rule_no"], c["heading"] = heading_rule[c["part"].strip().lower()], c["part"]

rows = []
for c in chunks:
    body = "\n".join(c["body"]).strip()
    if not body and not c["heading"]:
        continue
    meta = doc_meta.get(c["file_name"], {"title": c["file_name"], "doc_type": "other",
                                         "edition": None, "authority_rank": 99})
    rule_word = "By-Law" if meta["doc_type"] == "bylaws" else "Rule"
    rule_label = f"{rule_word} {c['rule_no']} {c['heading']}" if c["rule_no"] else (c["part"] or "General")
    header = f"{meta['title']} | {rule_label} | pages {c['page_start']}-{c['page_end']}"
    # Split long sections, repeating the header so every piece stands alone
    pieces = [body[i:i + MAX_CHARS] for i in range(0, max(len(body), 1), MAX_CHARS)]
    for i, piece in enumerate(pieces):
        content = f"{header}\n{piece}"
        rows.append({
            "chunk_id": hashlib.sha1(f"{c['file_name']}|{rule_label}|{c['page_start']}|{i}|{content}".encode()).hexdigest(),
            "file_name": c["file_name"],
            "doc_title": meta["title"],
            "doc_type": meta["doc_type"],
            "edition": meta["edition"],
            "authority_rank": int(meta["authority_rank"]),
            "rule_no": c["rule_no"],
            "heading": c["heading"] or c["part"],
            "page_start": c["page_start"],
            "page_end": c["page_end"],
            "content": content,
            "embedding": None,
        })
rows = list({r["chunk_id"]: r for r in rows}.values())  # identical sections (e.g. repeated headings) share an id
print(f"{len(rows)} chunks from {len({r['file_name'] for r in rows})} document(s)")

spark.sql(f"""
CREATE TABLE IF NOT EXISTS {chunks_tbl} (
  chunk_id STRING NOT NULL, file_name STRING, doc_title STRING, doc_type STRING, edition STRING,
  authority_rank INT, rule_no STRING, heading STRING, page_start INT, page_end INT, content STRING,
  embedding ARRAY<FLOAT>)
""")
if "embedding" not in spark.table(chunks_tbl).columns:
    spark.sql(f"ALTER TABLE {chunks_tbl} ADD COLUMNS (embedding ARRAY<FLOAT>)")
spark.createDataFrame(rows, schema=spark.table(chunks_tbl).schema).createOrReplaceTempView("new_chunks")
spark.sql(f"""
MERGE INTO {chunks_tbl} t USING new_chunks s ON t.chunk_id = s.chunk_id
WHEN NOT MATCHED THEN INSERT *
WHEN NOT MATCHED BY SOURCE THEN DELETE
""")
spark.sql(f"""COMMENT ON TABLE {chunks_tbl} IS 'Teeball (tee-ball, T-ball) rules knowledge base for TBAWA umpires: every rule and umpire interpretation from the TBAWA Official Rule Book (Edition 8), the Annotated Rule Book & Umpire Manual (Edition 7A) and the Wembley Tee-Ball Association (WTBA) By-Laws (local competition rules), one row per rule section. Answer teeball rules questions with the ask_rulebook function.'""")
for col, comment in {
    "rule_no": "Teeball rule number, e.g. 17.10 (tag up after catch), 13.1 (foul ball), 20 (obstruction). For WTBA By-Laws (doc_type bylaws) this is the by-law number, a separate numbering",
    "doc_type": "rulebook (TBAWA Rule Book), manual (Annotated Manual) or bylaws (Wembley WTBA local by-laws)",
    "heading": "Rule heading / title, e.g. Forced Play, Obstruction, Fair Batted Balls",
    "content": "Full rule text with document title, rule number and page numbers",
    "doc_title": "Source document: TBAWA Official Rule Book Ed 8, Annotated Rule Book & Umpire Manual Ed 7A, or WTBA By-Laws July 2026",
    "authority_rank": "1 = Rule Book (wins on conflict), 2 = Annotated Manual, 3 = WTBA By-Laws (local rules for Wembley competition games)",
}.items():
    spark.sql(f"ALTER TABLE {chunks_tbl} ALTER COLUMN {col} COMMENT '{comment}'")
# Clean up duplicate ids left by earlier runs
spark.sql(f"""
INSERT OVERWRITE {chunks_tbl}
SELECT * FROM {chunks_tbl} QUALIFY row_number() OVER (PARTITION BY chunk_id ORDER BY embedding IS NULL) = 1
""")
# Keyword rarity (IDF) per word, used by hybrid retrieval in ask_rulebook
spark.sql(f"""
CREATE OR REPLACE TABLE {catalog}.{schema}.rule_terms
COMMENT 'Inverse document frequency of words in rule_chunks (keyword weights for hybrid retrieval)' AS
WITH terms AS (
  SELECT DISTINCT chunk_id, w
  FROM {chunks_tbl} LATERAL VIEW explode(split(regexp_replace(lower(content), '[^a-z0-9.]+', ' '), ' ')) x AS w
  WHERE length(w) >= 3
)
SELECT w AS word, ln(n / (1 + count(*))) AS idf
FROM terms CROSS JOIN (SELECT count(*) AS n FROM {chunks_tbl}) GROUP BY w, n
""")

# ai_query is non-deterministic so can't be used in UPDATE/MERGE directly; materialise embeddings first
to_embed = spark.sql(f"""
SELECT chunk_id, ai_query('{embedding_endpoint}', content) AS embedding
FROM {chunks_tbl} WHERE embedding IS NULL
""").collect()
if to_embed:
    spark.createDataFrame(to_embed, "chunk_id STRING, embedding ARRAY<FLOAT>").createOrReplaceTempView("new_embeddings")
    spark.sql(f"""
    MERGE INTO {chunks_tbl} t USING new_embeddings s ON t.chunk_id = s.chunk_id
    WHEN MATCHED THEN UPDATE SET t.embedding = s.embedding
    """)
print(f"embedded {len(to_embed)} new chunk(s)")
