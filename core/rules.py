"""Nosana LLM: plain-English rules -> machine-checkable assertions (fixed vocabulary)."""
from __future__ import annotations

import json
import re

from . import llm

VOCAB = {
    "excluded_from_items": ["person", "item_ids"],
    "excluded_from_category": ["person", "category"],
    "pays_nothing": ["person"],
    "fixed_amount": ["person", "cents"],
    "cap": ["person", "max_cents"],
    "item_shared_by": ["item_id", "persons"],
    "weight": ["item_id", "weights"],
}

PROMPT = """You convert bill-splitting rules into assertions.
Use ONLY these assertion types and fields:
- excluded_from_items: person, item_ids (list)   -> person pays 0 for these items
- excluded_from_category: person, category       -> person pays 0 for items of that category (food/drink/dessert/other)
- pays_nothing: person                           -> person's total is 0
- fixed_amount: person, cents                    -> person's total is exactly cents
- cap: person, max_cents                         -> person's total is at most max_cents
- item_shared_by: item_id, persons (list)        -> only these people share the item
- weight: item_id, weights (object person->number) -> item split in these proportions

People: {people}
Items (id, name, category): {items}
Rules: "{rules}"

Return ONLY a JSON object:
{{"assertions": [...], "uncertain_items": [{{"item_id": "...", "why": "..."}}], "questions": ["..."]}}
Each assertion is a flat object with a "type" field, e.g. {{"type": "pays_nothing", "person": "Priya"}}.
Use exact person names and item ids from above. Money in integer cents. Do not invent rules that were not stated.
If the rules just say split equally, return an empty assertions list."""


def normalize(a):
    """Accept {"type": t, ...} and the shapes small models also emit: {t: {...}} and {t: "Priya"}."""
    if not isinstance(a, dict) or "type" in a or len(a) != 1:
        return a
    (t, v), = a.items()
    if isinstance(v, dict):
        return {"type": t, **v}
    if t in VOCAB and len(VOCAB[t]) == 1:
        return {"type": t, VOCAB[t][0]: v}
    return a


def validate(assertions: list, people: list[str], item_ids: list[str]) -> tuple[list[dict], list[str]]:
    ok, dropped = [], []
    for a in assertions if isinstance(assertions, list) else []:
        a = normalize(a)
        t = a.get("type") if isinstance(a, dict) else None
        if t not in VOCAB or any(f not in a for f in VOCAB[t]):
            dropped.append(f"unknown or incomplete: {a}")
            continue
        persons = [a["person"]] if "person" in a else a.get("persons", []) or list(a.get("weights", {}))
        items = a.get("item_ids", []) + ([a["item_id"]] if "item_id" in a else [])
        if any(p not in people for p in persons) or any(i not in item_ids for i in items):
            dropped.append(f"names/ids not on receipt: {a}")
            continue
        ok.append({k: a[k] for k in ["type", *VOCAB[t]]})
    return ok, dropped


def mentioned(item_name: str, rules_text: str) -> bool:
    """True if any whole word of the item name (3+ letters, plural-insensitive) appears in the rules text."""
    text_words = {w.rstrip("s") for w in re.findall(r"[a-z]+", rules_text.lower())}
    return any(w.rstrip("s") in text_words for w in re.findall(r"[a-z]+", item_name.lower()) if len(w) >= 3)


def ground(assertions: list[dict], receipt: dict, rules_text: str) -> tuple[list[dict], list[str]]:
    """Drop per-item share rules (item_shared_by, weight) on items the user never mentioned.
    Small models copy e.g. a beer weight onto every drink; this catches that deterministically."""
    names = {i["id"]: i["name"] for i in receipt["items"]}
    ok, dropped = [], []
    for a in assertions:
        if a["type"] in ("item_shared_by", "weight") and not mentioned(names.get(a["item_id"], ""), rules_text):
            dropped.append(f"not mentioned in your rules: {a['type']} on '{names.get(a['item_id'])}'")
        else:
            ok.append(a)
    return ok, dropped


def interpret(rules_text: str, receipt: dict, people: list[str]) -> dict:
    items = [(i["id"], i["name"], i.get("category")) for i in receipt["items"]]
    msg = PROMPT.format(people=people, items=items, rules=rules_text.replace('"', "'"))
    last_err = None
    for _ in range(2):  # retry once on parse failure
        text, model, secs = llm.chat([{"role": "user", "content": msg}], max_tokens=1200)
        try:
            data = llm.parse_json(text)
            break
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
    else:
        raise RuntimeError(f"Model did not return JSON for the rules: {last_err}")
    assertions, dropped = validate(data.get("assertions", []), people, [i[0] for i in items])
    assertions, ungrounded = ground(assertions, receipt, rules_text)
    dropped += ungrounded
    return {"assertions": assertions, "dropped": dropped, "model": model, "seconds": secs,
            "uncertain_items": data.get("uncertain_items", []), "questions": data.get("questions", [])}
