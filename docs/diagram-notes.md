# Infra diagram notes (for image generation)

## v3 render: current (docs/images/architecture-v3.png)
v2 + edit pass below: arrow now points down from "Knowledge base" to "Find rules + answer", orange icon moved from "Parse & chunk" to "Find rules + answer", phone aligned with bottom lane. All pending tweaks done.

## v2 render (superseded)
Generated from the Option A prompt below. Good: two lanes tell the story, return arrow with "Fair ball (Rule 14c)" shows the payoff, no clutter. Blog-ready as is.

Pending tweaks (next image-generation session):
1. Vertical arrow points UP into "Knowledge base" (reads like writing); should point DOWN to "Find rules + answer" (or be two-way).
2. Same Databricks icon on "Parse & chunk" and "Genie agent" makes them look alike; drop it from "Parse & chunk", or add it to "Find rules + answer" too (where the work happens).
3. Phone sits slightly lower than the bottom-lane boxes.

Edit-pass prompt:
> Keep everything the same, but: reverse the vertical arrow so it points down from "Knowledge base" to "Find rules + answer"; remove the orange icon from "Parse & chunk"; add the same small orange layered icon to the left of "Find rules + answer". No other changes.

## v2: simplified (prompt)
Feedback on v1 (ChatGPT render of the prompt below): too noisy. Four equal-weight columns with sub-steps, flow direction misleading (Answer → Genie → phone; the question actually starts on the phone), confusing dashed return arrow, busy bottom banner and stray icons. Fix: tell the two things that happen. Ingestion runs **once per rulebook**; answering runs **every question**. 5 boxes, ≤3-word labels, details go in the blog text/caption.

Option A: two lanes (recommended)
```
BUILD ONCE      [Rulebook PDFs] → [Parse & chunk] → [(Knowledge base)]
                                                          ↑
EVERY QUESTION  [Phone] → [Genie agent] → [Find rules + answer] ┘
                   ↖──────────── "Fair ball (Rule 14c)" ────────┘
```
Option B: single line
```
[Rulebook PDFs] → [Knowledge base] ← [Genie agent] ← [Phone]
                                     answer with rule number → phone
```

Prompt (Option A):
> Minimal flat architecture diagram, 16:9, white background, lots of empty space. Two horizontal lanes with small grey lane labels on the left: "Build once" (top) and "Every question" (bottom).
> Top lane, left to right: a PDF document icon labelled "Rulebooks" → a rounded box labelled "Parse & chunk" → a database cylinder labelled "Knowledge base".
> Bottom lane, left to right: a smartphone with a chat bubble "Fair or foul?" → a rounded box with a small orange layered icon labelled "Genie agent" → a rounded box labelled "Find rules + answer", with a vertical arrow up to "Knowledge base".
> One curved arrow returns from "Find rules + answer" to the phone, carrying a small chat bubble "Fair ball (Rule 14c)".
> Only these 6 labels, no other text. Colours: dark navy #1B3139 outlines and text, orange-red #FF3621 for arrows and the Genie icon, light grey #F5F5F5 lane backgrounds. Thin lines, sans-serif font, no gradients, no shadows, no extra icons or decorations.

Tips: if extra text appears, follow up "remove all text except the 6 labels"; if the return arrow gets messy, generate without it and add in an edit pass. Optional second, technical diagram for the "how it's built" section can reuse v1 below.

---

## v1 (original, too detailed)

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
- "Declarative Automation Bundles": `databricks bundle deploy -t dev | prod`
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
> Flat, clean technical architecture diagram, 16:9, white background, Databricks colours (orange-red #FF3621, dark navy #1B3139, light grey panels). Four zones left to right, titled "Source", "Ingest", "Answer", "Ask". Source: a GitHub repo box with two PDF icons labelled "Rule Book Ed 8" and "Manual 7A". Ingest: a job box with three steps, "Copy to Volume", "Parse → Chunk → Embed", "Create function", feeding a table icon labelled "rule_chunks". Answer: a box labelled "ask_rulebook(question)" with steps "Embed question", "Similarity search (SQL)", "LLM answer + citations". Ask: a box "Genie Agent: Teeball Umpire" connected to a smartphone showing a chat bubble "Fair or foul?" and a reply "Fair ball (Rule 14c)". Arrows connect the zones; a dashed arrow returns the answer to the phone. Bottom banner: "Declarative Automation Bundles: dev | prod", with a shield icon "Unity Catalog". Small badge: "Nothing always-on". Minimal teeball icons (ball on a tee). Short, legible labels, no extra text.

Tips: generate the layout first, then fix labels in a second edit pass ("change label X to Y"). Produce one variant without the crossed-out alternatives and one with them for the "what didn't work" section.
