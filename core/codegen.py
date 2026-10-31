"""Nosana LLM writes (and repairs) a stdlib-only Python split script per receipt."""
from __future__ import annotations

import json

from . import llm

CONTRACT = """The script must:
- Use Python standard library only. No network. No file writes.
- Read input.json from the current directory: {"receipt": {...}, "people": [...], "payer": "...", "rules_text": "...", "assertions": [...]}
  receipt has items [{id, name, qty, line_cents, category}], subtotal_cents, service_charge_cents, gst_cents, total_cents.
- Print exactly ONE line of JSON to stdout and nothing else:
  {"allocations": [{"item_id": "i1", "person": "Arun", "cents": 1600}, ...],
   "charges": [{"person": "Arun", "service_cents": 160, "gst_cents": 158}, ...],
   "totals": {"Arun": 1918, "Priya": 0, ...}}
- All money is integer cents. Every person in people must appear in charges and totals (0 allowed).
- In allocations, "item_id" is an item id like "i1" and "person" is a name from people. Never swap them.
Start from this template. Keep lr_split and the bottom half exactly as they are.
Your job is to write item_weights() so it applies EVERY assertion and every rule in rules_text:
  excluded_from_items / excluded_from_category / pays_nothing -> that person's weight is 0 for those items (all items for pays_nothing)
  item_shared_by -> only the listed persons have weight 1, everyone else 0
  weight -> use exactly the given weights, everyone else 0
```python
import json

def lr_split(total, weights):
    # Largest remainder: split integer total by {person: weight}; result sums EXACTLY to total.
    w = {p: x for p, x in weights.items() if x > 0}
    s = sum(w.values())
    if total == 0 or s == 0:
        return {p: 0 for p in weights}
    raw = {p: total * x / s for p, x in w.items()}
    out = {p: int(v) for p, v in raw.items()}
    for p in sorted(raw, key=lambda q: raw[q] - out[q], reverse=True)[:total - sum(out.values())]:
        out[p] += 1
    return {p: out.get(p, 0) for p in weights}

d = json.load(open("input.json"))
r, people, A = d["receipt"], d["people"], d["assertions"]
for a in A:  # every field always present, so a["person"] etc. never raise KeyError
    for k, v in {"person": None, "persons": [], "item_id": None, "item_ids": [], "category": None, "weights": {}}.items():
        a.setdefault(k, v)

def item_weights(item):
    # Who shares this item and in what proportion. APPLY THE RULES HERE.
    # Replace w for item_shared_by / weight first, then zero out exclusions. Example for weight:
    #   if a["type"] == "weight" and a["item_id"] == item["id"]: w = {p: a["weights"].get(p, 0) for p in people}    w = {p: 1 for p in people}
    return w

alloc = {}
for it in r["items"]:
    w = item_weights(it)
    if sum(w.values()) <= 0:
        w = {p: 1 for p in people}
    alloc[it["id"]] = lr_split(it["line_cents"], w)
items_p = {p: sum(alloc[i][p] for i in alloc) for p in people}
svc = lr_split(r["service_charge_cents"], items_p)
gst = lr_split(r["gst_cents"], items_p)
print(json.dumps({
    "allocations": [{"item_id": i, "person": p, "cents": c} for i, a in alloc.items() for p, c in a.items() if c],
    "charges": [{"person": p, "service_cents": svc[p], "gst_cents": gst[p]} for p in people],
    "totals": {p: items_p[p] + svc[p] + gst[p] for p in people},
}))
```"""


def _input_obj(receipt, people, payer, rules_text, assertions):
    return {"receipt": receipt, "people": people, "payer": payer,
            "rules_text": rules_text, "assertions": assertions}


def generate(receipt, people, payer, rules_text, assertions) -> tuple[str, str, float]:
    inp = _input_obj(receipt, people, payer, rules_text, assertions)
    msg = (f"Write a Python script that splits this restaurant bill.\n\n{CONTRACT}\n\n"
           f"Rules in plain English: {rules_text!r}\n"
           f"Confirmed assertions that MUST hold: {json.dumps(assertions)}\n\n"
           f"This is the input.json for this run (read it from the file, do not hard-code values):\n"
           f"{json.dumps(inp)}\n\nReturn only the code in one ```python block.")
    text, model, secs = llm.chat([{"role": "user", "content": msg}])
    return llm.parse_code(text), model, secs


def repair(script, fails, output_tail, receipt, people, payer, rules_text, assertions) -> tuple[str, str, float]:
    inp = _input_obj(receipt, people, payer, rules_text, assertions)
    msg = (f"This bill-splitting script failed independent checks run by a verifier you cannot see or change.\n\n"
           f"FAILED CHECKS:\n" + "\n".join(f"- {f}" for f in fails) +
           f"\n\nLast output / error from the sandbox (tail):\n{output_tail}\n\n"
           f"SCRIPT:\n```python\n{script}\n```\n\n{CONTRACT}\n\n"
           f"Confirmed assertions that MUST hold: {json.dumps(assertions)}\n"
           f"input.json for this run: {json.dumps(inp)}\n\n"
           f"Fix the script so every check passes. Do not change the output contract. "
           f"Return the full corrected script in one ```python block.")
    text, model, secs = llm.chat([{"role": "user", "content": msg}])
    return llm.parse_code(text), model, secs
