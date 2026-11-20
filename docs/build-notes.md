# Build notes: Teeball umpire agent on Databricks

Running log for a possible blog post. Newest findings appended at bottom of each section.

## Blog post notes (author)
Not writing yet; raw material.

### Story / motivation
- Been umpiring for my son's teeball team for the past few weeks. Several versions of rulebooks around; many rules hard to find and understand.
- Gave AI a go. Started with a local Claude (Claude Code) session: it read the whole rulebook each time and answered questions post-game. During games I noted questions down, ran them through Claude after the game. Aim: improve my knowledge, be a better umpire long term.
- Wanted something more persistent + accessible on the day, from my phone.
- Also wanted to experiment with something that scales: all rules fit in one prompt, but build it as if the rules were much longer, e.g. vast documentation for a bigger company, multiple use cases.
- Include the agent's prompts in the post: `src/prompts/answer_rules.md` (answering prompt inside `ask_rulebook`) + Genie agent instructions/description in `resources/genie.yml`.

### Why not just Claude Code on mobile? (arguments, to refine)
For Databricks:
- **Shareable + governed**: Genie agent can be opened to other people (other umpires, the local teeball association, a club account) via groups/permissions; per-user access, audit, usage tracking. A local Claude Code session is tied to my machine/repo; Claude Code on mobile/web is a developer tool tied to a repo + environment, not something to hand to other umpires. (Nuance: claude.ai Team/Enterprise Projects can be shared within an org, but every person needs a seat in that org; not a fit for an association of volunteers on personal plans.)
- **Business-user UX**: Genie One mobile = chat for non-technical users, no repo, no terminal, no prompt engineering.
- **Scales past the context window**: retrieval over chunks instead of loading the whole rulebook each question; adding more rulebooks/editions = drop a PDF and run the job.
- **Cost per question**: reading ~55k tokens of rulebook every question vs top-10 chunks; everything pay-per-use, nothing always-on.
- **Single source of truth**: rulebook versions + precedence (Ed 8 beats 7A) managed as data in a pipeline, same answers for everyone.
- **Learning goal**: how far Databricks Free + Genie One get you; patterns transfer to company docs (HR policies, runbooks, product docs).
For Claude Code (be honest):
- Better model (Claude vs open GPT-OSS-120B on Free), better reasoning on tricky edge cases and grey areas.
- Remembers past questions: CLAUDE.md rulings log grows each session, follow-ups build on earlier rulings.
- Zero infra: setup was minutes vs a pipeline, function, agent, permissions.

### Key message: evaluation is where the value comes in (stress this)
- The 12 questions I'd already asked the local Claude session, with answers I'd checked and trusted, became a ready-made eval set for free. Real questions from real games, not synthetic ones.
- With that, judging the system needed no manual testing: one bundle job (`eval_rulings`) runs every question through the agent, an LLM judge compares each answer with the trusted ruling, and the scores go into a table.
- That turned "does it feel right?" into numbers, and every change became measurable: chunking fixes, citation prompt, model swap, hybrid retrieval. Try something → run the eval → keep it or drop it.
- Cheap to iterate: comparing 3 models was one parameter change (`models=a|b|c`) and a few minutes of pay-per-use compute; experiments use temporary variants of the same SQL template, so the live agent isn't touched.
- Lesson: before building the clever bit, keep a small set of questions with answers you trust. The earlier local Claude Q&A wasn't throwaway work; it became the yardstick for everything after.
- **Plot twist: verify the evaluator too.** The "best" model (Qwen, 8/12) returned empty strings for every question; the LLM judge graded them CORRECT anyway. Only spotted by reading the raw answers. Lesson: add deterministic checks (empty answer, missing citation) before the LLM judge, and spot-read results, especially surprising wins.
- Caveats worth stating: LLM judges vary (self-judge harsher than Maverick), single runs are noisy, 12 questions is small; still far better than eyeballing answers on a phone.

### Is 12 questions enough? (blog question)
- No: fine for catching regressions (empty answers) and direction, not precise scores. One question = ~8 points.
- Rough 95% margin at ~75% accuracy: n=12 ±~25 pts; n=100 ±~8.5; n=400 ±~4. 5→9 correct is encouraging but within noise; GPT-OSS hybrid runs 7/7/9 correct show variance.
- Scaling the eval set: LLM-generate Q/A per rule chunk (~270 chunks → 300-500 Qs), umpire spot-checks a sample, add real Genie conversation questions over the season, stratify by rule section, repeat runs 3x and average.

### Why not just use Claude Opus reading the whole rulebook? (THE blog question)
- Honest answer: for this rulebook you could. ~55k tokens fits in context; it produced the 12 reference answers; long context avoids retrieval misses (which most iterations fixed).
- Retrieval wins when: corpus outgrows the context window (hundreds of docs, company knowledge, many associations' rulebooks); cost per question (whole corpus each question vs ~5-10k tokens to a cheaper model; prompt caching narrows gap for a fixed small corpus, not a growing one); platform/governance (Free Edition has no Claude; data stays governed in UC, shareable to groups, audited).
- Paid Databricks has Claude via FMAPI → "long-context mode" inside ask_rulebook is a valid design for small corpora: a choice, not dogma.
- Best of both: expensive whole-document model = **teacher** (generates + annotates large eval set, human spot-checks); cheap scalable RAG = **student** scored against it.
- Caveat: grading vs Opus measures agreement with Opus, not truth. My review of the 12 is what made them trustworthy; bigger Opus-made set needs human spot checks.
- Possible experiment: add `long_context` mode to eval (GPT-OSS on Free, Claude on paid) → compare accuracy + tokens/cost per question vs hybrid RAG; turns this question into numbers.

### Building a bigger eval set (teacher/student)
- Attempt 1, in-platform teacher: bundle job `generate_evalset`; GPT-OSS-120B with the full Rule Book + target rule text writes 2 Qs per topic (~120 topics). Pilot (10 topics, 20 Qs): most good game-situation questions, BUT near-duplicates (3 versions of "ball lands foul"), wrong expected answers (deflection over dead-ball line → "at-bat continues"; spectator interference cited as 18.1 runner interference), incoherent scenarios ("first contacts the line… still in the air"). Est. 75-80% usable.
- Added quality gates: embedding dedupe (cosine > 0.93) + independent checker (Llama 4 Maverick, same context) marking VALID/INVALID. Re-pilot: 19 kept, 1 duplicate, **0 invalid**; checker looks too lenient (same lesson as the judge: LLM graders are generous).
- Attempt 2, Opus 5.5 as teacher (the user's idea): the same Claude session that produced the trusted 12, reading BOTH full documents. 4 parallel sub-agents by rule area (1-9, 10-16, 17, 18-22 + Manual-only umpiring content), ~60-70 Qs each, instructed to verify each answer against the text and drop uncertain ones. Model diversity: Opus writes, GPT-OSS answers, Maverick judges. Trade-off: one-off step outside the platform (not schedulable), uses Claude tokens; Databricks generator kept as the in-platform option.

- Opus result: 302 questions in ~6 min wall-clock (4 agents in parallel, ~530k sub-agent tokens total); 1 near-duplicate dropped (same base-runner-charging scenario written for 15(e) and 17.14) → **301 questions** (102 easy / 145 medium / 54 hard) in `evals/opus.json`. Age cut-off pair (born 10 May vs 20 July 2018) kept on purpose.
- Side effect worth a blog paragraph: writing a test exposed gaps/conflicts in the rulebooks themselves. Agents deliberately left out questions the documents can't settle:
  - 16.6 batting out of the box: Rule Book "dead at contact" vs Manual "wait for possible catch".
  - Manual batting-out-of-order scenario 5 contradicts Rule Book 16.3(a).
  - Untouched fair ball rolling over the dead ball line: not covered by Rule 15.
  - 17.9 force after batter reaches 1st (known); 19.3(g) penalty list differs (Manual adds (c)); catcher's mitts legal on Manual p72 vs illegal on p19; Rule Book 2.3(f) cites a nonexistent "9.1d"; Manual fielder numbering typo; 7.4 bat stamp new in Ed 8.
  - Unclear in the documents: 2nd pitcher offence per pitcher or per team; "one extra base" beyond what.
- Flagged for human review: #2005 follows the Manual (plate umpire final on 3rd/home) where Rule Book 1.2c says umpires don't overrule each other.
- Human spot-check: `docs/eval-review.md`: 25 stratified random (seed 2026) + flagged #2005. **Umpire (me) reviewed: all 26 correct**, incl. #2005 → Opus teacher trusted as ground truth (vs GPT-OSS teacher pilot ~75-80% usable).

- **Iteration 9 result: live agent (GPT-OSS + hybrid) on 301 Opus questions** (judge Maverick): **208 CORRECT (69%) / 72 PARTIAL (24%) / 21 WRONG (7%)**, rule cited 295/301. 95% margin now ~±5 pts (vs ±25 with 12 Qs). The 12-question 9/3/0 (75% correct) was in the right ballpark but optimistic.
  - By area: Rule 17 base running 82% correct / 8% wrong; Rules 1-9 78% / 3%; Rules 10-16 71% / 7%; **Rules 18-22 + Manual umpiring 48% / 10%** (weakest: signals, positioning, procedures live in unnumbered Manual sections, the chunks retrieval and citation handle worst).
  - By difficulty: easy 70% C / 6% W, medium 68% / 6%, hard 70% / 13% W (hard questions fail harder).
  - Wrong ids: 2033 2038 3004 3027 3033 3057 3071 4023 4031 4041 4051 4063 4067 5001 5002 5004 5012 5023 5039 5057 5082. Note 3027 (tee foul but caught = out) is the ruling-10 pattern again.
  - Bigger eval → targeted improvement: next iteration should focus on Manual-only umpiring content (chunk headings/section names, retrieval of unnumbered sections), not on base running which is already strong.

- **Iteration 10: cross-check at scale (301 Qs, judge Maverick)**: re-test the 12-question conclusions.

  | Model | Retrieval | Correct | Partial | Wrong | Empty | Cites rule |
  |---|---|---|---|---|---|---|
  | GPT-OSS-120B | hybrid (run 1) | 69.1% | 23.9% | 7.0% | 0 | 98.0% |
  | GPT-OSS-120B | hybrid (run 2) | 64.8% | 29.6% | 5.6% | 0 | 98.3% |
  | GPT-OSS-120B | vector | 65.4% | 26.9% | 7.6% | 0 | 97.7% |
  | Qwen 3.5 122B | hybrid | 55.5% | 30.6% | 14.0% | 32 | 87.7% |
  | Qwen 3.5 122B | vector | 55.5% | 31.2% | 13.3% | 27 | 89.4% |

  - **Held up:** GPT-OSS beats Qwen clearly (~+10 pts correct, half the wrong rate). Qwen still returns ~10% empty answers even at max_tokens 8000.
  - **Did NOT hold up:** "hybrid beats vector". Overall hybrid (avg 67%) vs vector (65%) is within run-to-run noise (same config: 69.1 vs 64.8 = ~4 pts variance). The 12-question jump 5→7 correct was mostly noise.
  - **But per area, hybrid isn't neutral; it's a trade-off** (GPT-OSS, hybrid avg of 2 runs vs vector):
    - Rules 1-9: 73% vs 64% (+9)
    - Rules 10-16: 69% vs 61% (+8)
    - Rule 17: 79% vs 78% (=)
    - Rules 18-22 + Manual umpiring: **49% vs 59% (-10)**
  - Likely cause: the rule-number boost + keyword scoring favour numbered Rule Book chunks, pushing out unnumbered Manual umpiring sections (signals, positioning, procedures). Points straight at the next improvement.
  - Blog lesson: small evals give direction, big evals give the truth, and per-segment breakdowns show trade-offs an overall score hides. Also: always run the same config twice to know your noise floor.

- **Iteration 11: diagnose before fixing (retrieval vs generation)**
  - Added a retrieval-only eval (`eval_retrieval` job): for each of the 301 questions, does the selected context contain the question's rule (or Manual section)? No LLM answer/judge, so fast and nearly free. Template gained `output="retrieval"`.
  - Result r0: recall vector 92% (hit@3 84%), **hybrid 95% (hit@3 89%)**, siblings 95% (85%). Weak area (rules 18-22 + Manual): vector 89% (76%), hybrid 90% (**86%**).
  - Surprise: retrieval is NOT the bottleneck. Hybrid finds the right section more often in the weak area, yet answers there were worse. Problem is generation.
  - Reading the failures: (1) answer prompt over-applied "Rule Book wins" + "prefer citing the Rule Book first", so the model dismissed Manual interpretations the umpire relies on (5002 runner hit by ball not out if fielder not fielding; 5057 overruling exception); (2) "ruling in one line" format → procedure questions ("what should the umpire do") lost the Manual's exact calls, signals, order of steps; (3) hybrid ranks Rule Book chunks first → model anchors on them.
  - Also found: practical Manual section (pp 51-73) is fragmented: sub-headings (SAFE, OUT, TIME) lose their parent "Signals", sentences detected as headings, the OBSTRUCTION *signal* inherited rule 20. Chunking fix queued after the prompt test.
  - Fix 1: `answer_rules_v2.md`: Rule Book wins only on direct contradiction, otherwise use Manual interpretations/procedures; procedure questions → ordered steps with exact calls/signals. Eval job gained `prompt` and `id_prefix` params to A/B prompts on a slice (area 5 = 83 Qs) before a full run.

  - Prompt v2 results. Area 5 slice first (83 Qs, hybrid): Manual-only 43% → 64% correct; rules 18-22 54% → 72% (wrong 12% → 4%). Then full 301:

    | Prompt | Retrieval | Correct | Partial | Wrong |
    |---|---|---|---|---|
    | v1 | hybrid (2 runs) | 66.9% | 26.7% | 6.3% |
    | **v2** | **hybrid** | **73.8%** | 20.9% | **5.3%** |
    | v1 | vector | 65.4% | 26.9% | 7.6% |
    | v2 | vector | 73.4% | 19.3% | 7.3% |

    By area (hybrid v1 → v2): R1-9 73 → 75, R10-16 69 → 78, R17 79 → 81 (wrong 6 → 10%, ~3 Qs, watch), R18-22+Manual 49 → 63 (wrong 9 → 2%). +7-8 pts overall ≈ 2× the run-to-run noise → real. Biggest single gain of the project came from the prompt, not the model or retrieval.
  - Fix 2: chunking v2 for the practical Manual section: ALL-CAPS sub-headings keep parent ("Signals > SAFE", "Practical Umpiring At The Plate > TEE FOUL"); emphasised sentences tagged as headings stay inline; fragments < 300 chars merge into the previous chunk of the same section; only top-level sections inherit rule numbers (OBSTRUCTION *signal* no longer "Rule 20"). 267 → 247 chunks. Tested on **dev** (separate schema) so prod evals weren't disturbed. Retrieval r1 (hybrid): ALL 95% / hit@3 90% (was 95/89); R18-22+Manual 92% / 88% (was 90/86). Small retrieval gain; answer eval pending.

### Blog lessons from iterations 9-13 (accuracy work)
1. **Measure where it fails before fixing.** Big eval + per-area breakdown pointed at Manual umpiring content; the overall score hid it.
2. **Split retrieval from generation.** A cheap retrieval-only eval (no LLM) showed search already found the right section ~95% of the time. Without it we'd have tuned search (the obvious suspect) and missed the real problem.
3. **The prompt was the biggest lever.** One instruction ("Rule Book wins") was over-applied and made the model ignore the Manual's practical guidance. Rewording it was worth more than swapping models or retrieval methods.
4. **Not every sensible fix moves the needle.** Chunking v2 was clearly cleaner but changed answers within noise; shipped anyway (no harm, better data), and said so honestly.
5. **Know your noise floor.** Same config twice differs by ~4 pts on 301 Qs, so only trust bigger changes, or average several runs.
6. **Test on dev, compare on prod.** Bundle targets let the chunking experiment run on a separate schema while prod evals ran undisturbed.

### Cleanup + the orphan agent (blog: why everything should be in the bundle)
- `bundle destroy -t dev`: 7 resources deleted (dev Genie agent, 4 jobs, schema with 5 tables, volume). Dev eval data lost by design; numbers already in these notes. Prod untouched.
- Afterwards the workspace still had **two** "Teeball Umpire" agents. The extra one (created 19 Nov 02:44 UTC) was from the very first ad-hoc `genie create-space` API probes, before the move to DABs: the probe returned an error, yet a space had been created. It pointed at the PDF volume (Genie file search, unavailable on Free) and was invisible to the bundle.
- **Genie One routed 3 of my real mobile questions to it** instead of the bundle-managed agent (tag-up after a catch ×2, do all runners need to be on base for Time), so they bypassed our rulebook pipeline. The API shows those messages COMPLETED but with no answer attachments, so the answers aren't recoverable. Questions exported to `docs/orphan-agent-conversations.json` (candidate eval questions).
- Deleted manually with `genie trash-space` (bundle couldn't, since it never managed it).
- Lessons: (1) anything created outside the bundle becomes invisible drift that `destroy` won't clean up; (2) an error response doesn't guarantee nothing was created; check; (3) duplicate agent names confuse Genie One routing; (4) the "everything via DABs" rule (adopted after this probe) is what made the rest of the project reproducible and cleanly destroyable.

### Disadvantages of the Genie solution
- **No memory across conversations**: Genie One can't keep track of questions I asked previously. The local Claude session kept them (rulings log in CLAUDE.md) and reused them. Genie conversations persist per user (web/mobile/desktop) but the agent doesn't learn from them.
- **Workaround (planned, not built)**: scheduled job harvests conversations via Genie API (`genie list-conversations --include-all` + messages + thumbs feedback) → LLM summarises good Q&As into the rulings format → reviewer approves → approved rulings written as md into the volume / a `rulings` table → ingest job chunks + embeds them → future answers retrieve past rulings. Bonus: rulings become shared knowledge across all users, not just mine.
- Open models only on Free (no Claude); Genie routing needed tuning (description + table comments); answer shown twice (function result + Genie summary); file-based Genie features unavailable on Free.
- **But**: with Genie One I can open access to more people (e.g. an account for the local teeball association) via a group like `business_us`. With my local Claude setup I couldn't do that.

## Iteration log (story arc: start simple, measure, improve)
Same 12 questions (`evals/rulings.json`, from my real post-game questions) used as the yardstick throughout. Every eval run stored in `eval_runs` (label, model, mode, judge, verdict per question).

| # | Iteration | What changed | Result |
|---|---|---|---|
| 0 | Local Claude Code | Whole rulebook (~55k tokens) in context, rulings log in CLAUDE.md | Produced the 12 reference answers. Accurate, but laptop-only, single user, re-reads everything each question |
| 1 | Genie native file search | Attach UC volume of PDFs to a Genie agent | Blocked on Free: `REQUIRED_MODEL_NOT_AVAILABLE` |
| 2 | Own pipeline + Vector Search | ai_parse_document → rule chunks → VS index → `ask_rulebook` SQL function → Genie | Function worked directly; from Genie `PERMISSION_DENIED` on index (grant didn't help). Endpoint always-on |
| 3 | Embeddings in Delta | gte-large-en embeddings stored in `rule_chunks`, cosine similarity in SQL | Works from Genie + mobile, nothing always-on |
| 3a | Routing | Keyword-rich agent description + table/column comments | Genie One mobile chat now finds the agent |
| 3b | Chunking + citations | Sub-headings (SCENARIO/OUTCOME/CONCLUSION) kept inline; unnumbered Manual sections inherit Rule Book numbers by heading; top-5 per document; stricter citation prompt | Obstruction answer went from "Manual CONCLUSION p48" to "Rule 20" |
| 4 | First accuracy check | GPT-OSS-120B answering + judging itself | 5 correct / 3 partial / 4 wrong, rule cited 9/12 |
| 5 | Independent judge + model comparison | Judge = Llama 4 Maverick; answer models GPT-OSS-120B vs Qwen 3.5 122B vs Llama 3.3 70B (vector retrieval) | GPT-OSS 5C/7P/0W (cites 11/12); **Qwen 8C/4P/0W** (cites 7/12); Llama 3.3 3C/9P/0W (cites 12/12). Maverick judge more lenient than self-judge (GPT-OSS 4 WRONG → 0). Single runs are noisy |
| 6 | Hybrid retrieval | + IDF keyword score + rule-number boost; + sibling expansion (all chunks of a retrieved rule, both docs) | GPT-OSS: vector 5C/7P/0W → **hybrid 7C/4P/1W, cites 12/12** (earlier partial run 7C/2P/3W) → hybrid+siblings 5C/6P/1W (more context ≠ better). #5 (foot on base) fixed by hybrid |
| 7 | **Eval caught lying** | Inspected Qwen's "winning" 8C: ALL 36 Qwen answers were EMPTY; judge still graded 8/12 CORRECT | Qwen 3.5 is a reasoning model: `max_tokens` 2000 consumed by hidden thinking → `''`. Fix: max_tokens 8000; eval marks empty answers WRONG without judging. Bonus: Qwen asked without context gives the *baseball* fair/foul rule (wrong for teeball) → grounding matters. Re-run with real answers: Qwen vector 6C/4P/2W (cites 10/12), hybrid 5C/5P/2W, hybrid+siblings 5C/5P/2W → GPT-OSS + hybrid stays best |
| 8 | Ship the winner | `retrieval_mode` default → `hybrid`, model GPT-OSS-120B, max_tokens 8000; redeploy dev + prod; eval as regression check | **9C/3P/0W, cites 12/12** (from 5C/3P/4W self-judged / 5C/7P/0W independent at iteration 4-5). Genie re-checked on #5 (previously WRONG) → Out. GPT-OSS hybrid runs so far: 7/2/3, 7/4/1, 9/3/0 → expect run-to-run variance |
| 9 | Bigger eval set | Opus 5.5 (whole docs) writes 301 Qs; umpire spot-checks 26 → all correct | Live config on 301: 69% C / 24% P / 7% W. Margin ±5 instead of ±25. Weak area found: Rules 18-22 + Manual umpiring (48% C) |
| 10 | Re-test conclusions at scale | 4 variants × 301 Qs | GPT-OSS ≫ Qwen confirmed; hybrid vs vector overall = noise (same config 69.1 vs 64.8); per area hybrid +9 on R1-16 but -10 on Manual umpiring |
| 11 | Diagnose: retrieval or generation? | Retrieval-only eval (recall of the right rule/section, no LLM) | Recall 92-95%, hybrid better even in the weak area → the bottleneck was the **prompt**, not search. Prompt v2 (Manual-aware, procedures as steps): **+8 pts overall (66.9 → 73.8% C)**, Manual area 49 → 63%, wrong 9 → 2% |
| 12 | Chunking v2 | Parent section on sub-headings, merge fragments, no sentence-headings, no false rule numbers | Retrieval slightly up (weak area hit@3 86 → 88%); answers within noise (70.8% vs 73.8%). Kept: cleaner data, no measured harm |
| 13 | Ship | Prompt v2 + chunking v2 live (dev + prod), final eval ×2 for a firm number | **75.1% / 76.1% correct (avg 75.6%), wrong 6.3% / 4.7%, rule cited 99%**. By area (avg of 2): R1-9 78%, R10-16 83%, R17 76%, R18-22+Manual 67% (was 48%). Two runs only 1 pt apart. Overall journey on 301 Qs: 66.9% → 75.6% correct; weakest area 48% → 67%. Genie check: tee-foul procedure answer now has the exact calls + signal from the Manual |

Engineering bits worth mentioning: shared SQL template (`src/jobs/rag_sql.py`) builds both the deployed function and temporary eval variants, so experiments don't touch the live agent; eval is a bundle job with parameters (`models`, `modes`, `judge`, `label`).
Gotchas: serverless python tasks have no `__file__` (pass code dir as arg); `bundle run --params` splits on commas (use `|` in list values). SQL UDFs can't reference the parameter inside nested subqueries (`ACCESSING_OUTER_QUERY_COLUMN_IS_NOT_ALLOWED`) → first hybrid attempt (IDF computed per question via subqueries) failed; fix = precompute IDF in a `rule_terms` table at ingest, reference the question once in a CTE, score with higher-order array functions (`filter`, `aggregate`) + a vocab map.

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

## Prod + permissions
- Dev-mode resources are real + visible to owner/admins (only names prefixed). Admins always see all Genie agents → can't hide dev from yourself with permissions; two near-identical agents may hurt Genie One routing.
- Solution: separate test user in `business_us` group; prod-only grants via `targets.prod.resources` overrides: Genie `permissions: group_name business_us, CAN_RUN`; schema `grants: business_us [USE_SCHEMA, SELECT, EXECUTE]`. Catalog USE_CATALOG + warehouse CAN_USE already granted to all workspace users on Free.
- Gotcha: `databricks groups list` doesn't return members (showed 0) — use `groups get <id>`.
- Prod ingest: 284 chunks parsed + embedded in one run. Mobile test as business user: works, routes to "Teeball Umpire" (after keyword-rich description + table comments).

## Accuracy check (12 logged rulings, prod, first run)
- Bundle job `eval_rulings`: `evals/rulings.json` (question + expected ruling from CLAUDE.md log) → `ask_rulebook` → LLM judge (GPT-OSS-120B, same model; self-grading bias caveat) → `eval_results` table, appended per run.
- Run 1: **5 CORRECT, 3 PARTIAL, 4 WRONG**; rule cited in 9/12.
- Failure analysis (manual review agrees with judge):
  - #2 Time / inside diamond: reasoning error; model equated "infield position" with "inside the diamond" despite citing the right definition.
  - #5 foot on edge of base: retrieval miss; 17.8 note (tag base with any part of body) not retrieved → "not covered".
  - #9 when not forced: model followed Manual 17.9 ("force ends once batter reaches 1st"), which is the known Rule Book vs Manual conflict (ruling 7). Expected answer should arguably require flagging the conflict.
  - #10 too much tee + caught: retrieval miss on 13.1 note / 14(d) (caught = fair) → answered "strike". Same question via Genie earlier answered "Out", so results are unstable with retrieval/model variance.
  - Partials (#4, #7, #11): right ruling, missing conditions (force removal, force vs non-force).
- Compare: local Claude session with the whole rulebook in context got all of these "right" (they ARE the reference answers), a real trade-off for the post: scale + sharing vs accuracy on a small corpus.
- Improvement ideas: hybrid keyword + vector retrieval (rule numbers, terms like "tee", "force"), retrieve rule notes with parent rule, larger k, query rewriting, stronger model (try Llama 4 Maverick / Qwen 3.5 122B on Free), judge with a different model than the answerer.

## Open questions / to revisit
- Does Genie reliably route questions to `ask_rulebook`, and render text not a 1-cell table?
- GPT-OSS answer quality vs Claude (baseline = rulings log in CLAUDE.md, use as eval set).
- Learning loop (rulings from conversations) parked; mobile access is priority.
