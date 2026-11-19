# databricks-teeball-agent

Teeball umpire rules assistant on Databricks. Rulebook PDFs → searchable knowledge base → Genie agent you can ask from the Databricks mobile app. Deployed with Databricks Asset Bundles; nothing always-on.

## How it works
1. `knowledge/rulebooks/*.pdf` synced with the bundle; `ingest_rulebooks` job copies them to the `kb_docs` volume.
2. `ai_parse_document` → rule-level chunks (`rule_chunks`) → `databricks-gte-large-en` embeddings stored in Delta.
3. `ask_rulebook(question)` SQL function: embeds question, cosine similarity in SQL, top 10 chunks → `databricks-gpt-oss-120b` answer with rule/page citations.
4. Genie agent "Teeball Umpire" routes questions to `ask_rulebook` via a parameterised example SQL.

## Deploy
```sh
databricks bundle deploy            # -t prod for clean names
databricks bundle run ingest_rulebooks
```
New rulebook edition: add PDF to `knowledge/rulebooks/`, update `knowledge/documents.json`, deploy + run.

## Layout
- `databricks.yml` – bundle, variables, targets
- `resources/` – schema/volume, ingest job, Genie agent
- `src/jobs/` – copy, parse/chunk/embed, create function
- `src/prompts/answer_rules.md` – answer instructions
- `docs/build-notes.md` – build log / findings
- `CLAUDE.md`, `knowledge/text/` – local Claude Code rules reference + rulings log
