"""SQL template for the ask_rulebook pipeline, shared by create_function.py and eval_rulings.py.

Retrieval modes:
- vector:          cosine similarity on stored embeddings, top 5 chunks per document
- hybrid:          vector + IDF-weighted keyword overlap + boost for rule numbers named in the question
- hybrid_siblings: hybrid, then also pull every chunk with the same rule number (rule notes, Rule Book <-> Manual)
"""

MODES = ("vector", "hybrid", "hybrid_siblings")
PER_DOC = 5
TOP_K = 10
MAX_CHUNKS = 16
KEYWORD_WEIGHT = 0.15
RULE_NO_BOOST = 0.2
STOPWORDS = ("the", "and", "but", "for", "are", "was", "his", "her", "their", "they", "them", "what", "when",
             "does", "did", "has", "have", "had", "with", "into", "from", "that", "this", "then", "than",
             "who", "why", "how", "can", "not", "its", "out", "call", "ruling", "rule", "runner", "ball")


def sql_str(s):
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def function_body(catalog, schema, llm_endpoint, embedding_endpoint, prompt, mode="vector", output="answer"):
    """Body of a SQL UDF with one STRING parameter named `question`.

    output="answer": the LLM's answer text. output="retrieval": JSON array of the selected chunks
    (rule_no, heading, file) in rank order, without calling the LLM, to measure retrieval on its own.
    output="prompt": the full LLM prompt, for a UDF that also takes `question_embedding ARRAY<FLOAT>`;
    no ai_query inside, so non-owners can call it (see answer_sql).
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode}")
    question_vector = "question_embedding" if output == "prompt" else f"ai_query({sql_str(embedding_endpoint)}, question)"
    chunks = f"{catalog}.{schema}.rule_chunks"
    cosine = """aggregate(zip_with(c.embedding, q.v, (a, b) -> a * b), 0D, (acc, x) -> acc + x)
               / (sqrt(aggregate(c.embedding, 0D, (acc, x) -> acc + x * x))
                  * sqrt(aggregate(q.v, 0D, (acc, x) -> acc + x * x)))"""

    if mode == "vector":
        scored = f"""
      SELECT c.chunk_id, c.file_name, c.rule_no, c.heading, c.content, {cosine} AS score
      FROM {chunks} c, q
      WHERE c.embedding IS NOT NULL"""
    else:
        # Keyword weights come from rule_terms (precomputed IDF); the question is referenced only once (in q),
        # because SQL UDFs can't use the outer `question` inside nested subqueries
        kw_weight = "coalesce(vocab.idf[w], vocab.max_idf)"
        scored = f"""
      SELECT c.chunk_id, c.file_name, c.rule_no, c.heading, c.content,
             {cosine}
             + {KEYWORD_WEIGHT} * coalesce(
                 aggregate(filter(q.words, w -> instr(lower(c.content), w) > 0), 0D, (acc, w) -> acc + {kw_weight})
                 / nullif(aggregate(q.words, 0D, (acc, w) -> acc + {kw_weight}), 0), 0)
             -- by-laws number separately, so "rule 17" must not boost By-Law 17
             + CASE WHEN c.rule_no IS NOT NULL AND c.doc_type <> 'bylaws'
                     AND q.ql RLIKE concat('(^|[^0-9.])', replace(c.rule_no, '.', '\\\\.'), '([^0-9]|$)')
                    THEN {RULE_NO_BOOST} ELSE 0 END AS score
      FROM {chunks} c, q, vocab
      WHERE c.embedding IS NOT NULL"""

    top = f"""
    top AS (
      SELECT * FROM scored
      -- top N per document so the (shorter) Rule Book sections aren't crowded out by the Manual
      QUALIFY row_number() OVER (PARTITION BY file_name ORDER BY score DESC) <= {PER_DOC}
      ORDER BY score DESC
      LIMIT {TOP_K}
    )"""

    if mode == "hybrid_siblings":
        # Window functions only (joins/aggregates over `top` inside a SQL UDF fail to decorrelate)
        top = f"""
    ranked AS (
      SELECT *, row_number() OVER (PARTITION BY file_name ORDER BY score DESC) AS rn_doc FROM scored
    ),
    flagged AS (
      SELECT *, rn_doc <= {PER_DOC}
                AND row_number() OVER (ORDER BY CASE WHEN rn_doc <= {PER_DOC} THEN 0 ELSE 1 END, score DESC) <= {TOP_K}
                AS in_top
      FROM ranked
    ),
    with_rule_best AS (
      -- best top-hit score of each rule; NULL if no chunk of that rule made the top
      SELECT *, max(CASE WHEN in_top THEN score END) OVER (PARTITION BY coalesce(rule_no, chunk_id)) AS rule_best
      FROM flagged
    )"""
        selected = """
    selected AS (
      -- top hits, plus every other chunk of the same rule (any document) ranked just below its best hit
      SELECT content, rule_no, heading, file_name, CASE WHEN in_top THEN score ELSE rule_best - 0.001 END AS score
      FROM with_rule_best
      WHERE in_top OR (rule_no IS NOT NULL AND rule_best IS NOT NULL)
    )"""
    else:
        selected = "\n    selected AS (SELECT content, rule_no, heading, file_name, score FROM top)"

    ctes = f"""
  WITH q AS (
      SELECT {question_vector} AS v, lower(question) AS ql,
             filter(array_distinct(split(regexp_replace(lower(question), '[^a-z0-9.]+', ' '), ' ')),
                    w -> length(w) >= 3 AND NOT array_contains(array({', '.join(sql_str(w) for w in STOPWORDS)}), w)) AS words),
    vocab AS (
      SELECT map_from_entries(collect_list(struct(word, idf))) AS idf, max(idf) AS max_idf
      FROM {catalog}.{schema}.rule_terms),
    scored AS ({scored}
    ),{top},{selected}"""
    ranked = f"slice(sort_array(collect_list(struct(-score AS s, content AS c, rule_no AS r, heading AS h, file_name AS f))), 1, {MAX_CHUNKS})"

    full_prompt = f"concat({sql_str(prompt)}, '\\n\\nRULE EXCERPTS:\\n', excerpts, '\\n\\nQUESTION: ', question)"
    excerpts = f"""SELECT concat_ws('\\n\\n---\\n\\n', transform({ranked}, x -> x.c)) AS excerpts
    FROM selected"""
    if output == "prompt":
        return f"""({ctes}
  SELECT {full_prompt}
  FROM ({excerpts})
)"""
    if output == "retrieval":
        return f"""({ctes}
  SELECT to_json(transform({ranked}, x -> named_struct('rule_no', x.r, 'heading', x.h, 'file', x.f)))
  FROM selected
)"""
    return f"""({ctes}
  SELECT {llm_call(llm_endpoint, full_prompt)}
  FROM ({excerpts})
)"""


def llm_call(llm_endpoint, prompt_expr):
    # generous max_tokens: reasoning models (e.g. Qwen 3.5) spend tokens thinking and return '' if cut off
    return (f"ai_query({sql_str(llm_endpoint)}, {prompt_expr}, "
            "modelParameters => named_struct('temperature', 0.0, 'max_tokens', 8000))")


def answer_sql(catalog, schema, llm_endpoint, embedding_endpoint, question="question"):
    """Top-level statement that answers `question` via build_rule_prompt (the Genie agent's query, see genie.yml).

    ai_query fails with AI_FUNCTION_REGISTRY_LOOKUP_FAILURE for non-admins when it runs inside another user's
    SQL UDF, or when given modelParameters, so both model calls sit here, outside the function, with defaults.
    """
    prompt_expr = (f"{catalog}.{schema}.build_rule_prompt({question}, "
                   f"ai_query({sql_str(embedding_endpoint)}, {question}))")
    return f"SELECT ai_query({sql_str(llm_endpoint)}, {prompt_expr}) AS answer"
