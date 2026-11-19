"""(Re)create the ask_rulebook SQL function: embed question, rank rule_chunks by cosine similarity, LLM answer with citations.

Plain SQL over stored embeddings (no Vector Search endpoint), so it is pay-per-use and runs from Genie.

Args: catalog schema llm_endpoint embedding_endpoint prompt_file
"""
import sys

from pyspark.sql import SparkSession

catalog, schema, llm_endpoint, embedding_endpoint, prompt_file = sys.argv[1:6]
spark = SparkSession.builder.getOrCreate()


def sql_str(s):
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


with open(prompt_file) as f:
    prompt = f.read()

spark.sql(f"""
CREATE OR REPLACE FUNCTION {catalog}.{schema}.ask_rulebook(
  question STRING COMMENT 'The umpire''s teeball rules question, in plain English')
RETURNS STRING
COMMENT 'Answers a teeball umpire rules question from the TBAWA Rule Book and Annotated Manual, with rule and page citations. Use for ANY question about teeball rules, rulings, umpiring or game situations.'
RETURN (
  SELECT ai_query(
    {sql_str(llm_endpoint)},
    concat({sql_str(prompt)}, '\\n\\nRULE EXCERPTS:\\n', excerpts, '\\n\\nQUESTION: ', question),
    modelParameters => named_struct('temperature', 0.0, 'max_tokens', 2000))
  FROM (
    SELECT concat_ws('\\n\\n---\\n\\n', collect_list(content)) AS excerpts
    FROM (
      SELECT c.content, c.file_name,
             aggregate(zip_with(c.embedding, q.v, (a, b) -> a * b), 0D, (acc, x) -> acc + x)
               / (sqrt(aggregate(c.embedding, 0D, (acc, x) -> acc + x * x))
                  * sqrt(aggregate(q.v, 0D, (acc, x) -> acc + x * x))) AS score
      FROM {catalog}.{schema}.rule_chunks c,
           (SELECT ai_query({sql_str(embedding_endpoint)}, question) AS v) q
      WHERE c.embedding IS NOT NULL
      -- top 5 per document so the (shorter) Rule Book sections aren't crowded out by the Manual
      QUALIFY row_number() OVER (PARTITION BY c.file_name ORDER BY score DESC) <= 5
      ORDER BY score DESC
      LIMIT 12
    )
  )
)
""")
print(f"created {catalog}.{schema}.ask_rulebook")
