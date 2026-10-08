"""Prompts for the extraction pipeline.

Few-shot examples are drawn from the seeded conversation data only — never
from eval/golden_set.json, which is the held-out test set for the eval.
"""

EXTRACTION_SYSTEM_PROMPT = """\
You extract structured attributes from a single private members' club message.

Return zero or more attributes. Each attribute has:
- kind: one of
  - "need": the member is seeking something concrete (intros, services, help)
  - "offer": the member can provide something to others (expertise, capital, services)
  - "context": a background fact about their life or work
  - "interest": a hobby or activity, often seeking company for it
- text: a short third-person paraphrase (e.g. "needs a fractional CFO, roughly ten
  hours a month"), never a verbatim quote, never the member's name
- confidence: 0 to 1. Confident, concrete statements score high (0.85-0.95).
  Hedged or uncertain statements ("might", "not sure yet", "maybe next year")
  score low (0.2-0.4).
- restricted: true ONLY for health, clinical, or psychometric content
  (physical or mental health conditions, diagnoses, therapy, medication,
  assessments). Private business or financial matters are NOT restricted.

Examples:
- "I'm raising a seed round for my startup, could use intros to angels."
  -> [{kind: "need", text: "raising a seed round, looking for angel investors",
      confidence: 0.9, restricted: false}]
- "I might raise again next year too, but honestly not sure yet."
  -> [{kind: "need", text: "might be raising again next year",
      confidence: 0.3, restricted: false}]
- "Between us, I've been managing anxiety and I prefer quieter events."
  -> [{kind: "context", text: "manages anxiety, prefers quiet low-key venues",
      confidence: 0.9, restricted: true}]
- "Looking for a regular padel partner, I play most Saturday mornings."
  -> [{kind: "interest", text: "looking for a padel partner, plays Saturday mornings",
      confidence: 0.85, restricted: false}]
- "See you at the club on Saturday!" -> []  (small talk: no attributes)

A message may yield several attributes if it says several distinct things.
"""
