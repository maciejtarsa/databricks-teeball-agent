"""LLM judge prompt shared by eval_rulings.py and eval_genie.py."""

JUDGE_PROMPT = """You grade a teeball rules assistant. Compare the ANSWER with the EXPECTED ruling written by an experienced umpire.
Grade:
- CORRECT: same ruling and covers the key conditions in EXPECTED.
- PARTIAL: ruling right but misses or muddles an important condition, or hedges where EXPECTED is clear.
- WRONG: different ruling, or a key fact contradicts EXPECTED.
Also check citations: CITES_RULE = YES if the answer cites at least one rule number that appears in EXPECTED (or the same rule), else NO.
Reply in exactly two lines:
VERDICT: CORRECT|PARTIAL|WRONG; CITES_RULE: YES|NO
REASON: <one sentence>"""
