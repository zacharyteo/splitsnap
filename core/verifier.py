"""Fixed, hand-written verifier. NEVER generated or edited by the model.

All money is integer cents. Each check returns human-readable failure strings;
those exact strings are what the repair prompt receives.
"""
from __future__ import annotations

SC_RATE_PCT = 10  # Singapore service charge
GST_RATE_PCT = 9  # Singapore GST, applied on subtotal + service charge

SPLIT_CHECKS = {
    "S1": "Output is valid JSON with allocations, charges, totals",
    "S2": "Every item allocated exactly (to the cent)",
    "S3": "Shares sum exactly to the receipt total",
    "S4": "Exactly the listed people appear",
    "S5": "No negative amounts",
    "S6": "Every confirmed rule (assertion) holds",
    "S7": "Each person's total = items + service + GST",
    "S8": "Service and GST shares sum to the printed charges",
}
RECEIPT_CHECKS = {
    "R1": "Items sum to the printed subtotal",
    "R2": "Service charge and GST match the 10% then 9% rates",
    "R3": "Subtotal + service + GST = printed total",
    "R4": "All amounts valid and positive",
}


def pct_round(cents: int, pct: int) -> int:
    """cents * pct / 100, rounded half-up (how SG receipts round)."""
    return (cents * pct + 50) // 100


def _i(x):
    return x if isinstance(x, int) and not isinstance(x, bool) else None


# ---------------------------------------------------------------- receipt
def verify_receipt(r: dict) -> list[str]:
    fails: list[str] = []
    items = r.get("items") or []
    sub = _i(r.get("subtotal_cents"))
    sc = _i(r.get("service_charge_cents", 0))
    gst = _i(r.get("gst_cents", 0))
    tot = _i(r.get("total_cents"))
    disc = _i(r.get("discount_cents", 0)) or 0

    # R4 first: everything else needs valid numbers
    bad = [f"{it.get('id')} '{it.get('name')}'" for it in items
           if _i(it.get("line_cents")) is None or it["line_cents"] <= 0]
    if not items:
        fails.append("R4 FAIL: no line items found")
    if bad:
        fails.append(f"R4 FAIL: items with missing or non-positive amount: {', '.join(bad)}")
    for k, v in [("subtotal_cents", sub), ("service_charge_cents", sc), ("gst_cents", gst), ("total_cents", tot)]:
        if v is None or v < 0:
            fails.append(f"R4 FAIL: {k} is missing or negative ({r.get(k)!r})")
    if fails:
        return fails

    item_sum = sum(it["line_cents"] for it in items)
    if abs(item_sum - disc - sub) > 1:
        disc_txt = f" minus discount {disc} = {item_sum - disc}" if disc else ""
        fails.append(f"R1 FAIL: items sum to {item_sum}{disc_txt}, "
                     f"printed subtotal is {sub} (off by {item_sum - disc - sub} cents)")
    if sc:
        exp_sc = pct_round(sub, SC_RATE_PCT)
        if abs(exp_sc - sc) > 2:
            fails.append(f"R2 FAIL: service charge printed {sc}, 10% of subtotal {sub} is {exp_sc}")
    if gst:
        exp_gst = pct_round(sub + sc, GST_RATE_PCT)
        if abs(exp_gst - gst) > 2:
            fails.append(f"R2 FAIL: GST printed {gst}, 9% of (subtotal+service) {sub + sc} is {exp_gst}")
    if abs(sub + sc + gst - tot) > 1:
        fails.append(f"R3 FAIL: subtotal {sub} + service {sc} + GST {gst} = {sub + sc + gst}, "
                     f"printed total is {tot}")
    return fails


# ---------------------------------------------------------------- split
def verify_split(receipt: dict, people: list[str], assertions: list[dict], out) -> dict:
    """Returns {"fails": [...], "passed": [check ids], "total": n_checks}."""
    fails: list[str] = []
    failed_ids: set[str] = set()

    def fail(cid, msg):
        failed_ids.add(cid)
        fails.append(f"{cid} FAIL: {msg}")

    # S1 shape
    if not isinstance(out, dict) or not all(k in out for k in ("allocations", "charges", "totals")):
        fail("S1", "output must be one JSON object with keys allocations, charges, totals; "
                   f"got {type(out).__name__} with keys {list(out)[:6] if isinstance(out, dict) else '-'}")
        return _result(fails, failed_ids, skip_rest=True)
    allocs, charges, totals = out["allocations"], out["charges"], out["totals"]
    try:
        alloc_rows = [(str(a["item_id"]), str(a["person"]), a["cents"]) for a in allocs]
        charge_rows = [(str(c["person"]), c["service_cents"], c["gst_cents"]) for c in charges]
        totals = {str(k): v for k, v in dict(totals).items()}
    except Exception as e:  # noqa: BLE001
        fail("S1", f"malformed rows ({type(e).__name__}: {e}). "
                   "allocations rows need item_id, person, cents; charges rows need person, service_cents, gst_cents")
        return _result(fails, failed_ids, skip_rest=True)
    nonint = [v for _, _, v in alloc_rows if _i(v) is None] + \
             [v for _, s, g in charge_rows for v in (s, g) if _i(v) is None] + \
             [v for v in totals.values() if _i(v) is None]
    if nonint:
        fail("S1", f"all amounts must be integer cents; found {nonint[:5]}")
        return _result(fails, failed_ids, skip_rest=True)

    items = {it["id"]: it for it in receipt["items"]}
    pset = set(people)

    # S5 no negatives
    neg = [f"{i}/{p}={c}" for i, p, c in alloc_rows if c < 0] + \
          [f"{p} charges" for p, s, g in charge_rows if s < 0 or g < 0] + \
          [f"{p}={v}" for p, v in totals.items() if v < 0]
    if neg:
        fail("S5", f"negative amounts: {', '.join(neg[:6])}")

    # S2 each item allocated exactly
    per_item: dict[str, int] = {}
    for i, p, c in alloc_rows:
        if i not in items:
            fail("S2", f"allocation references unknown item_id '{i}'")
        per_item[i] = per_item.get(i, 0) + c
    for iid, it in items.items():
        got = per_item.get(iid, 0)
        if got != it["line_cents"]:
            fail("S2", f"item {iid} '{it['name']}' line is {it['line_cents']}, allocations sum to {got} "
                       f"(off by {got - it['line_cents']})")

    # S3 totals == receipt total
    tsum = sum(totals.values())
    if tsum != receipt["total_cents"]:
        fail("S3", f"totals sum to {tsum}, receipt total is {receipt['total_cents']} "
                   f"(off by {tsum - receipt['total_cents']} cents)")

    # S4 people
    extra = [p for p in list(totals) + [p for _, p, _ in alloc_rows] + [p for p, _, _ in charge_rows] if p not in pset]
    missing = [p for p in people if p not in totals]
    if extra:
        fail("S4", f"unknown people in output: {sorted(set(extra))}")
    if missing:
        fail("S4", f"people missing from totals (include them with 0): {missing}")

    # S7 per-person consistency (discount allocation not modelled; skip if discount)
    alloc_by_p: dict[str, int] = {}
    for _, p, c in alloc_rows:
        alloc_by_p[p] = alloc_by_p.get(p, 0) + c
    svc_by_p: dict[str, int] = {}
    gst_by_p: dict[str, int] = {}
    for p, s, g in charge_rows:
        svc_by_p[p] = svc_by_p.get(p, 0) + s
        gst_by_p[p] = gst_by_p.get(p, 0) + g
    if not receipt.get("discount_cents"):
        for p in people:
            exp = alloc_by_p.get(p, 0) + svc_by_p.get(p, 0) + gst_by_p.get(p, 0)
            if p in totals and totals[p] != exp:
                fail("S7", f"{p}: total {totals[p]} but items {alloc_by_p.get(p, 0)} + service "
                           f"{svc_by_p.get(p, 0)} + GST {gst_by_p.get(p, 0)} = {exp}")

    # S8 charges sum to printed charges
    sc_sum, gst_sum = sum(svc_by_p.values()), sum(gst_by_p.values())
    if sc_sum != receipt.get("service_charge_cents", 0):
        fail("S8", f"service shares sum to {sc_sum}, printed service charge is {receipt.get('service_charge_cents', 0)}")
    if gst_sum != receipt.get("gst_cents", 0):
        fail("S8", f"GST shares sum to {gst_sum}, printed GST is {receipt.get('gst_cents', 0)}")

    # S6 assertions
    def a_of(p, iid):
        return sum(c for i, pp, c in alloc_rows if i == iid and pp == p)

    for a in assertions or []:
        t = a.get("type")
        p = a.get("person")
        if t == "excluded_from_items":
            for iid in a.get("item_ids", []):
                if a_of(p, iid):
                    fail("S6", f"rule '{p} excluded from {iid}' broken: {p} allocated {a_of(p, iid)} of {iid}")
        elif t == "excluded_from_category":
            for iid, it in items.items():
                if it.get("category") == a.get("category") and a_of(p, iid):
                    fail("S6", f"rule '{p} pays nothing for {a.get('category')}' broken: "
                               f"{p} allocated {a_of(p, iid)} of {iid} '{it['name']}'")
        elif t == "pays_nothing":
            if totals.get(p, 0) != 0:
                fail("S6", f"rule '{p} pays nothing' broken: {p} total is {totals.get(p)}")
        elif t == "fixed_amount":
            if totals.get(p) != a.get("cents"):
                fail("S6", f"rule '{p} pays exactly {a.get('cents')}' broken: total is {totals.get(p)}")
        elif t == "cap":
            if totals.get(p, 0) > a.get("max_cents", 0):
                fail("S6", f"rule '{p} pays at most {a.get('max_cents')}' broken: total is {totals.get(p)}")
        elif t == "item_shared_by":
            iid, allowed = a.get("item_id"), set(a.get("persons", []))
            bad = sorted({pp for i, pp, c in alloc_rows if i == iid and c and pp not in allowed})
            if bad:
                fail("S6", f"rule 'item {iid} shared only by {sorted(allowed)}' broken: also allocated to {bad}")
        elif t == "weight":
            iid, w = a.get("item_id"), a.get("weights", {})
            wsum = sum(w.values()) or 1
            line = items.get(iid, {}).get("line_cents", 0)
            for pp, wt in w.items():
                exp = line * wt / wsum
                if abs(a_of(pp, iid) - exp) > 1:
                    fail("S6", f"rule 'item {iid} split by weights {w}' broken: {pp} got {a_of(pp, iid)}, "
                               f"expected about {exp:.0f}")
    return _result(fails, failed_ids)


def _result(fails, failed_ids, skip_rest=False):
    ids = list(SPLIT_CHECKS)
    passed = [] if skip_rest else [c for c in ids if c not in failed_ids]
    return {"fails": fails, "passed": passed, "total": len(ids)}
