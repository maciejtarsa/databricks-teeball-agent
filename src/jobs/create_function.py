"""(Re)create the ask_rulebook SQL function: embed question, rank rule_chunks in SQL, LLM answer with citations.

Plain SQL over stored embeddings (no Vector Search endpoint), so it is pay-per-use and runs from Genie.

Args: catalog schema llm_endpoint embedding_endpoint prompt_file retrieval_mode src_dir
"""
import sys

from pyspark.sql import SparkSession

sys.path.insert(0, sys.argv[-1])  # src/jobs dir (serverless tasks have no __file__)
from rag_sql import function_body  # noqa: E402

catalog, schema, llm_endpoint, embedding_endpoint, prompt_file, mode = sys.argv[1:7]
spark = SparkSession.builder.getOrCreate()

with open(prompt_file) as f:
    prompt = f.read()

spark.sql(f"""
CREATE OR REPLACE FUNCTION {catalog}.{schema}.ask_rulebook(
  question STRING COMMENT 'The umpire''s teeball rules question, in plain English')
RETURNS STRING
COMMENT 'Answers a teeball umpire rules question from the TBAWA Rule Book and Annotated Manual, with rule and page citations. Use for ANY question about teeball rules, rulings, umpiring or game situations.'
RETURN {function_body(catalog, schema, llm_endpoint, embedding_endpoint, prompt, mode)}
""")
print(f"created {catalog}.{schema}.ask_rulebook (llm={llm_endpoint}, retrieval={mode})")
