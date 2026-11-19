# Infra diagram notes (for image generation)

## What the diagram must show
Left-to-right flow in 4 zones, plus a deploy bar along the bottom.

### Zone 1: Source (left)
- Laptop / GitHub repo icon: `databricks-teeball-agent`
- Inside: 2 PDF icons labelled "Rule Book Ed 8" and "Annotated Manual 7A", plus `documents.json` (edition + precedence)

### Zone 2: Ingest (Databricks, serverless job)
Job box "ingest_rulebooks" with 3 steps in sequence:
1. **Copy**: PDFs → Unity Catalog Volume `kb_docs`
2. **Parse, chunk, embed**:
   - `ai_parse_document` (PDF → elements with pages)
   - Rule-aware chunks (one per rule, e.g. "17.10 Leaving Base Early")
   - `ai_query` embeddings with `gte-large-en`
   - → Delta table `rule_chunks` (text + rule no + page + embedding)
3. **Create function**: `ask_rulebook(question)`

### Zone 3: Answer (Unity Catalog SQL function)
Box "ask_rulebook(question)" with 3 inner steps:
1. Embed the question (`gte-large-en`)
2. Cosine similarity in SQL → top 5 chunks per document
3. `ai_query` → GPT-OSS-120B writes the ruling with rule and page citations

Small icon: serverless SQL warehouse (auto-stop).

### Zone 4: Ask (right)
- Genie Agent "Teeball Umpire" (instructions + example SQL → calls `ask_rulebook`)
- Genie One chat → phone icon (Databricks mobile app) with a speech bubble: "Ball lands fair then rolls foul?" → "Fair ball (Rule 14c, p12)"
- Small person icon "umpire" holding the phone, on a teeball field

### Bottom bar: Deploy and govern
- "Databricks Asset Bundles": `databricks bundle deploy -t dev | prod`
- Two lanes: **dev** (owner only, prefixed names) and **prod** ("Teeball Umpire", group `business_us`: CAN_RUN + SELECT/EXECUTE)
- Unity Catalog shield icon (governance)

### Callouts (optional, small badges)
- "Nothing always-on: all serverless / pay-per-use"
- "Crossed out: Vector Search endpoint (always-on, not callable from Genie)"
- "Crossed out: Genie file search (unavailable on Free Edition)"

## Arrows
Repo → Volume → Parse/Chunk/Embed → `rule_chunks` → `ask_rulebook` ← Genie Agent ← Genie One ← Phone.
The question goes right-to-left and the answer comes back left-to-right; draw the answer as a dashed return arrow to the phone.

## Style
- Clean, flat tech-architecture style on a white background, rounded boxes, thin connectors, generous spacing.
- Databricks palette: orange-red `#FF3621`, navy `#1B3139`, light grey zone backgrounds; teeball accents (white ball with red stitching, a tee, a green/sand diamond) used sparingly as icons, not a full scene.
- Labels in a sans-serif font; code names (`rule_chunks`, `ask_rulebook`) in monospace.
- 16:9 landscape, readable at blog width (~1200px). Keep text short; image generators mangle long text.

## Example prompt (Nano Banana / Gemini image)
> Flat, clean technical architecture diagram, 16:9, white background, Databricks colours (orange-red #FF3621, dark navy #1B3139, light grey panels). Four zones left to right, titled "Source", "Ingest", "Answer", "Ask". Source: a GitHub repo box with two PDF icons labelled "Rule Book Ed 8" and "Manual 7A". Ingest: a job box with three steps, "Copy to Volume", "Parse → Chunk → Embed", "Create function", feeding a table icon labelled "rule_chunks". Answer: a box labelled "ask_rulebook(question)" with steps "Embed question", "Similarity search (SQL)", "LLM answer + citations". Ask: a box "Genie Agent: Teeball Umpire" connected to a smartphone showing a chat bubble "Fair or foul?" and a reply "Fair ball (Rule 14c)". Arrows connect the zones; a dashed arrow returns the answer to the phone. Bottom banner: "Databricks Asset Bundles: dev | prod", with a shield icon "Unity Catalog". Small badge: "Nothing always-on". Minimal teeball icons (ball on a tee). Short, legible labels, no extra text.

Tips: generate the layout first, then fix labels in a second edit pass ("change label X to Y"). Produce one variant without the crossed-out alternatives and one with them for the "what didn't work" section.
