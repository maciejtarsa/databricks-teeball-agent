# Build notes: Teeball umpire agent on Databricks

Running log for a possible blog post. Newest findings appended at bottom of each section.

## Goal
- Teeball umpire asks situational rules questions on phone, mid-game. Answers must cite rule numbers.
- Sources: TBAWA Rule Book Ed 8 (21 pp) + Annotated Rule Manual Ed 7A (73 pp). Rule Book wins on conflict.
- Started as Q&A with Claude Code in a local folder (PDFs + md text + CLAUDE.md with rulings log).
- Wanted: Databricks mobile app access, upload PDF → knowledge base, scales beyond 2 docs, deploy everything with DABs, built as if for a real multi-user account. Running on Databricks Free Edition.

## Path taken / decisions
1. **Plan v1: stuff everything in the prompt.** Corpus ~218KB (~55k tokens) fits one prompt; App + FMAPI. Rejected: wanted it to scale to more rulebooks.
2. **Plan v2: custom RAG** (parse → chunk → Vector Search → MLflow agent → App). Then looked at Genie One.
3. **Genie One research** (Nov 2026): Genie One = renamed Databricks One. Genie Agents = renamed Genie spaces. Mobile app supports Genie Agents + Databricks Apps. Genie Agents can attach UC volumes ("Content Search", Public Preview): auto `ai_parse_document`, page-level citations, no vector index to manage. Looked perfect.
4. **Free Edition test: Genie file search blocked.** `create-space` with a volume → `VolumeIndex cannot be enabled ... REQUIRED_MODEL_NOT_AVAILABLE`. No Previews page access on Free either.
5. **What does work on Free:** Genie spaces, `ai_parse_document`, `ai_query` (open models only: GPT-OSS 120B/20B, Llama 3.3 70B, Qwen, Gemma; no Claude), gte-large-en embeddings, 1 Vector Search endpoint.
6. **Chosen: Option 1 = Genie agent fronting our own retrieval.** Pipeline builds `rule_chunks` → Vector Search index → SQL function `ask_rulebook(question)` (vector_search + ai_query) → Genie agent attached to function + table. Native in mobile app.
   - Option 2 (not chosen): same retrieval + AppKit chat App. Better chat control, but Free stops Apps after 24h.
7. **Everything via DABs** (lesson: I created schema/volume ad hoc with CLI during a spike; deleted, redid in bundle).

## DABs notes
- CLI v0.274 had no `genie_space` resource; **v1.26 has `genie_spaces`, `vector_search_endpoints`, `vector_search_indexes`** (also `mcp_services`, `postgres_*`, etc.). Upgrade CLI first. Old pip `databricks-cli` 0.18 was shadowing on PATH; Homebrew now requires `brew trust databricks/tap`.
- Bundles can't put files into UC volumes. Workaround: PDFs synced with bundle to workspace files; job task copies `/Workspace/.../files/knowledge/rulebooks` → `/Volumes/...`. Serverless can read `/Workspace` and write `/Volumes` with plain Python.
- **Chicken-and-egg:** `vector_search_indexes` needs the source table at deploy time, but the table is created by a job. First-time order: deploy (no index) → run ingest → deploy (adds index) → run ingest again. No DAB `tables` resource to break the cycle.
- Resource keys must be unique across types (schema `teeball` + endpoint `teeball` clashed).
- Dev mode prefixes schema (`dev_maciej_tarsa_db_teeball`) and job names; Vector Search endpoint name wasn't prefixed. Free allows only 1 VS endpoint → dev and prod can't both own one.
- Genie `serialized_space`: version 2, 32-char hex ids, collections sorted by id. Volume source format is `data_sources.volumes: [{path: /Volumes/...}]` (found by probing; `identifier` rejected). Can be given inline as YAML in the bundle, so `${var}` interpolation works.

## Serverless job gotchas
- `sys.exit(0)` in a `spark_python_task` on serverless = task FAILED (SystemExit surfaced). Structure code to fall through instead.
- Index sync right after index creation → `BadRequest: Index is not ready to sync yet. Pipeline is in state RUNNING`. Retry loop needed.
- `bundle deploy` waits for index creation (~10+ min on Free first time, "pending endpoint provisioning"); local network timeout killed the wait but index got created and the next deploy adopted it.

## Ingestion / chunking
- `ai_parse_document` output: `document.elements[]` with `type` (section_header, text, figure, page_number, ...), `content`, `bbox[].page_id` (0-based). Figures get descriptions (e.g. bat certification stamps).
- Rule headers parse cleanly as `section_header` like `7.1 The Diamond` → chunk per rule via regex `^\d{1,2}(\.\d{1,2})?\s+`.
- Manual has sub-headers (`SCENARIO: 1`, `OUTCOME:`, `SPECIAL NOTE:`) that split rules and lost rule numbers → keep inline when inside a rule.
- Chunk content prefixed with `doc title | Rule X heading | pages a-b` so each chunk stands alone + embeds context. Long sections split at 2500 chars, header repeated.
- First pass: 106 chunks (rulebook) + 199 (manual); 273 after fix.
- Incremental: `parsed_docs` re-parses only new/changed PDFs; `rule_chunks` MERGE by deterministic chunk_id with `WHEN NOT MATCHED BY SOURCE THEN DELETE`; CDF on for Delta Sync.
- Doc precedence as data: `knowledge/documents.json` (title, doc_type, edition, authority_rank).

## Cost model (serverless vs always-on)
| Component | Billing | Always on? |
|---|---|---|
| Ingest job | serverless jobs compute | no, per run |
| `ai_parse_document`, `ai_query`, embeddings | pay-per-token | no |
| SQL warehouse (Genie + function) | serverless, auto-stop | no |
| Index sync | TRIGGERED pipeline | no |
| **Vector Search endpoint** | per hour while it exists | **yes** |

- Free Edition: no billing, but fair-use daily quota (exceed → compute off for day/month). Unclear if idle VS endpoint eats quota.
- Paid account alternatives to always-on VS:
  1. No vectors: SQL keyword/ILIKE search over `rule_chunks` in `ask_rulebook`. Fully pay-per-use; weaker semantic recall.
  2. **Embeddings in Delta** (recommended for paid): `ai_query('databricks-gte-large-en', content)` at ingest → `embedding ARRAY<FLOAT>`; function embeds question + brute-force cosine similarity in SQL. Pay-per-use, semantic, fine for thousands of chunks. Only `ask_rulebook` + one ingest step change; Genie unchanged.
  3. Keep VS endpoint (check scale-to-zero availability; unverified).

## Genie agent findings
- `ask_rulebook` called directly (SQL statement API) → good answers matching past rulings (fair/foul first landing, tag-up 17.10), with rule + page citations. GPT-OSS-120B fine so far.
- Genie chat mode IGNORED text instruction "always call ask_rulebook" + attached `sql_functions`; said table can't answer.
- Fix: parameterised example SQL (`instructions.example_question_sqls` with `parameters`, `description` must be an array) `SELECT ask_rulebook(:question)` → Genie now routes questions to the function.
- Next blocker: Genie-run query fails `PERMISSION_DENIED: Insufficient permissions for UC entity ...rule_chunks_index` even though same user owns index and same SQL works via statement API. Likely Genie's execution credentials don't carry vector_search access to the index.

- Granting `SELECT` on the index to `account users` (via bundle `grants` on the index) did NOT fix it → not a UC grant issue; Genie's query credentials apparently can't call the Vector Search API at all.
- Genie refuses ad-hoc SQL unrelated to its tables ("I can only answer questions about the available tables"), so can't use it to probe what functions work.
- Pivot: embeddings stored in `rule_chunks.embedding` (ai_query gte-large-en at ingest, only for new chunks) + cosine similarity in SQL inside `ask_rulebook`. Kills two birds: Genie-compatible (if ai_query works from Genie) + no always-on endpoint.

- **It worked.** Genie → `SELECT ask_rulebook(:question)` → ai_query embedding + SQL cosine + ai_query GPT-OSS → correct answers (17.13 force-out runs, obstruction, fair/foul). So ai_query IS callable from Genie; only vector_search() isn't. Genie shows function result + its own short text summary.
- Gotchas on the way: `UPDATE ... SET x = ai_query(...)` rejected (`INVALID_NON_DETERMINISTIC_EXPRESSIONS`) → collect embeddings to a DataFrame, MERGE. Deterministic chunk_id collided for identical sections → `DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE`; first run on empty table had silently inserted dupes. Copy step rewriting PDFs changed mtime → re-parse every run; fixed with filecmp skip.
- Removed Vector Search endpoint + index + sync task from bundle (`bundle plan` showed 2 deletes). Side benefit: the deploy/ingest/deploy chicken-and-egg is gone; fresh workspace = 1 deploy + 1 job run.
- Final architecture: PDF → volume → job (copy, ai_parse_document, rule chunks, gte-large-en embeddings in Delta, CREATE FUNCTION ask_rulebook) → Genie agent with example SQL routing to the function → mobile app. Nothing always-on; all pay-per-use (warehouse auto-stops).
- Retrieval at scale: brute-force cosine over all chunks per question. Fine for thousands of chunks; for much bigger corpora reconsider VS (with a non-Genie front end) or pre-filtering by doc/edition.

## Mobile app
- Mobile tabs: Chat, Dashboards, Apps, Schedules. No agent list; Genie One Chat auto-routes to a matching Genie Agent (docs: searches agents first, then data assets). Web search bar drop-down lets you pick an agent explicitly.
- First mobile question: Genie One said it had no teeball knowledge; user had to tell it an agent exists. Routing relies on agent title/description (+ likely table metadata). Fix attempt: keyword-rich agent description (teeball/tee-ball/T-ball, TBAWA, topic list) + COMMENT ON TABLE/columns for rule_chunks.

## Open questions / to revisit
- Does Genie reliably route questions to `ask_rulebook`, and render text not a 1-cell table?
- GPT-OSS answer quality vs Claude (baseline = rulings log in CLAUDE.md, use as eval set).
- Learning loop (rulings from conversations) parked; mobile access is priority.
