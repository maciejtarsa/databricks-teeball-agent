# databricks-teeball-agent

A teeball umpire rules assistant on Databricks. Rulebook PDFs go in, a Genie agent comes out that you can ask from the Databricks mobile app mid-game (workspace admins only, see [Limitations](#limitations)): "Runner leaves 1st on the hit and the ball is caught - out?" → ruling plus rule and page citations.

- Built on **Databricks Free Edition**, deployed entirely with **Declarative Automation Bundles (DABs)**.
- **Nothing always-on:** serverless jobs, pay-per-token models, no Vector Search endpoint.
- **Evaluation-first:** every change is measured against trusted answers (15 real-game rulings + 301 generated questions).

Sources: TBAWA (Teeball Association of Western Australia) Official Rule Book Ed 8, TBAWA Annotated Rule Book & Umpire Manual 7A, and Wembley Tee-Ball Association (WTBA) By-Laws July 2026.

![Architecture](docs/images/architecture-v3.png)

## How it works

1. **Ingest** (`ingest_rulebooks` job): PDFs in `knowledge/rulebooks/` are synced with the bundle and copied to a Unity Catalog volume.
2. **Parse & chunk:** `ai_parse_document`, then one chunk per rule/section, each prefixed with document, rule number and pages. Only new or changed PDFs are re-parsed.
3. **Embed:** `system.ai.gte-large-en` embeddings stored in a Delta column (`rule_chunks`), plus IDF keyword weights (`rule_terms`).
4. **Answer:** SQL function `ask_rulebook(question)` embeds the question, ranks chunks in plain SQL (cosine + keyword overlap + rule-number boost), takes the top 5 per document, and asks `system.ai.gpt-oss-120b` to answer with citations using `src/prompts/answer_rules_v3.md`.
5. **Agent:** Genie agent "Teeball Umpire" calls `ask_rulebook` via a parameterised example SQL. Its only table is `documents` (titles, no rule text), so it can't fall back to its own keyword query on the rule text. Genie One (web + mobile) routes teeball questions to it.

## Prerequisites

- Databricks workspace with Unity Catalog and serverless (Free Edition works).
- Databricks CLI **v1.26+** (for the `genie_spaces` bundle resource).
- A CLI profile named `teeball`:
  ```sh
  databricks auth login --host https://<your-workspace>.cloud.databricks.com --profile teeball
  ```
- A SQL warehouse named "Serverless Starter Warehouse" (Free Edition default) or override `warehouse_id`.
- Deploy as a workspace admin. The agent is admin-only (see [Limitations](#limitations)). Other admins who ask it need schema access (being a workspace admin doesn't grant Unity Catalog privileges): list them in `.databricks/bundle/prod/variable-overrides.json` (not committed):
  ```json
  {"agent_grants": [{"principal": "admin@example.com", "privileges": ["USE_SCHEMA", "SELECT", "EXECUTE"]}]}
  ```

## Deploy

```sh
databricks bundle deploy -t prod                 # dev (default) prefixes names and stays owner-only
databricks bundle run -t prod ingest_rulebooks   # parse, chunk, embed, create ask_rulebook
```

Then open the "Teeball Umpire" agent in your home folder, or ask Genie One. A new agent can take a few hours to appear in Genie One chat routing; opening it directly works immediately.

Quick test from SQL:
```sql
SELECT workspace.teeball.ask_rulebook('Ball first lands fair then rolls foul before 3rd. Fair or foul?');
```

## Add a document

1. Drop the PDF in `knowledge/rulebooks/`.
2. Add an entry to `knowledge/documents.json` (`title`, `doc_type`, `edition`, `authority_rank`; lower rank wins on conflict).
3. `databricks bundle deploy -t prod && databricks bundle run -t prod ingest_rulebooks`

Documents with their own numbering (e.g. `doc_type: bylaws`) are labelled separately so "Rule 17" never matches "By-Law 17".

## Evaluate

```sh
# Answer quality: LLM judge (Llama 4 Maverick) vs trusted answers -> eval_runs table
databricks bundle run -t prod eval_rulings --params "label=my-change"
databricks bundle run -t prod eval_rulings --params "label=my-change,evals_file=opus.json"

# Compare models / retrieval modes ('|' separates values; --params splits on commas)
databricks bundle run -t prod eval_rulings --params "label=models,models=system.ai.gpt-oss-120b|databricks-meta-llama-3-3-70b-instruct"

# Retrieval only (did the right rule reach the model?) -> retrieval_runs table
databricks bundle run -t prod eval_retrieval --params "label=my-change"

# End to end through the deployed agent (Genie Conversation API): did it call ask_rulebook? -> genie_runs table
databricks bundle run -t prod eval_genie --params "label=my-change"
```

Experiments run through temporary functions, so the live agent isn't touched. Empty answers are marked WRONG before judging.

- `evals/rulings.json`: 18 questions from real games, answers checked against the rulebooks.
- `evals/opus.json`: 301 questions written by Claude Opus 5.5 reading both full TBAWA documents, spot-checked by an umpire.

**Current results** (301 questions): ~75% correct, ~5% wrong, rule cited in 99% of answers. Rulings set: 12 correct / 5 partial / 1 wrong. The build log in `docs/build-notes.md` covers every iteration and what moved the numbers.

## Layout

| Path | What |
|---|---|
| `databricks.yml` | Bundle, variables, dev/prod targets, schema grants for other admins |
| `resources/` | Schema + volume, ingest job, evaluation jobs, Genie agent |
| `src/jobs/` | Copy PDFs, parse/chunk/embed, create function, evaluation jobs |
| `src/jobs/rag_sql.py` | Shared SQL template for `ask_rulebook` (retrieval modes, answer/retrieval output) |
| `src/prompts/` | Answer prompts (v1-v3), question-generation prompt |
| `knowledge/` | Source PDFs, extracted text, document metadata |
| `evals/` | Evaluation sets |
| `docs/` | Build notes, evaluation review, architecture diagram |
| `CLAUDE.md` | The original local Claude Code setup: rule summary + rulings log |

## Limitations

- Answers are a second opinion for volunteer umpires, not an official ruling. The umpire's call stands.
- Free Edition has no Claude models and no Genie file search; the answering model is GPT-OSS-120B.
- Brute-force similarity in SQL suits thousands of chunks, not millions.

### Admin-only: non-admin users can't use the agent

The agent works for workspace admins only. We tested this with the same user as a non-admin and as an admin (not the function's owner, with fewer grants): every failure below disappeared once the user was an admin, on the web and in the mobile app. Every call that business users (consumer access) made to `ask_rulebook` failed with `[AI_FUNCTION_REGISTRY_LOOKUP_FAILURE] Failed to dynamically resolve function "ai_query" from AI Function Registry. INTERNAL: An unknown error occurred.` Admins never see it, so it went unnoticed for days: Genie quietly fell back to its own keyword query on `rule_chunks` and answered from that.

What we found, testing as a non-admin user on Free Edition (2026-11-23):

| Problem | Workaround tried | Outcome |
|---|---|---|
| Consumer access only: `ai_query` fails everywhere | Add the Databricks SQL access entitlement | Direct `ai_query` works |
| `ai_query` inside a SQL function owned by another user fails for non-admins, even with SQL access (works for a non-owner admin) | Split the function: `build_rule_prompt` (no model calls) + both `ai_query` calls at the top of Genie's query | Function works; model calls move to the caller |
| `ai_query(..., modelParameters => ...)` fails for non-admins (even just `max_tokens`) | Drop `modelParameters` | GPT-OSS (a reasoning model) then hits the default output limit and returns NULL in roughly 1 of 4 answers |
| Entitlement changes don't reach open sessions; logging out isn't enough | Test in a new SQL editor tab / new Genie chat | Explained "worked, then failed" results |

Also learned along the way: an inline `ai_query` passed as a function argument can be re-evaluated (pass it as a scalar subquery), and with schema-wide SELECT, Genie One chat can query `rule_chunks` directly, bypassing the agent.

Every workaround worked, but together they left a fragile agent that still drops answers. So the agent stays admin-only with the simple `ask_rulebook` design, and nothing is granted to non-admins. We couldn't tell whether these are Free Edition limits or general platform bugs.

To open it up properly, move the model calls out of users' hands: for example a Databricks App (mobile-friendly, opens from the Databricks mobile app) that runs as a service principal and calls the models through the SDK with `max_tokens` set. The catch: an app runs, and costs, while it's started; it doesn't scale to zero like the serverless pieces here.

## License

Code: MIT (see `LICENSE`). The rulebooks and by-laws in `knowledge/` belong to TBAWA and WTBA; they're publicly available from those associations and included here for convenience only, not covered by the MIT license.
