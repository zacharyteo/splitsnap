"""Hand-written reference split (TEST ONLY). Proves the verifier accepts a correct split.
Stdlib only, same contract as the generated scripts: reads input.json, prints one JSON line."""
import json


def lr(total, weights):
    """Largest remainder: ints proportional to weights summing exactly to total."""
    names = [n for n, w in weights.items() if w > 0]
    if not names:
        return {}
    ws = sum(weights[n] for n in names)
    raw = {n: total * weights[n] / ws for n in names}
    out = {n: int(raw[n]) for n in names}
    left = total - sum(out.values())
    for n in sorted(names, key=lambda n: (-(raw[n] - out[n]), n))[:left]:
        out[n] += 1
    return out


def main():
    d = json.load(open("input.json"))
    r, people, A = d["receipt"], d["people"], d["assertions"]
    free = {a["person"] for a in A if a["type"] == "pays_nothing"}
    allocations, item_tot = [], {p: 0 for p in people}
    for it in r["items"]:
        elig = [p for p in people if p not in free]
        for a in A:
            if a["type"] == "excluded_from_items" and it["id"] in a["item_ids"]:
                elig = [p for p in elig if p != a["person"]]
            if a["type"] == "excluded_from_category" and it["category"] == a["category"]:
                elig = [p for p in elig if p != a["person"]]
            if a["type"] == "item_shared_by" and a["item_id"] == it["id"]:
                elig = [p for p in elig if p in a["persons"]]
        w = {p: 1 for p in elig}
        for a in A:
            if a["type"] == "weight" and a["item_id"] == it["id"]:
                w = dict(a["weights"])
        for p, c in lr(it["line_cents"], w).items():
            allocations.append({"item_id": it["id"], "person": p, "cents": c})
            item_tot[p] += c
    svc = lr(r["service_charge_cents"], item_tot) if r["service_charge_cents"] else {}
    gst = lr(r["gst_cents"], item_tot) if r["gst_cents"] else {}
    charges = [{"person": p, "service_cents": svc.get(p, 0), "gst_cents": gst.get(p, 0)} for p in people]
    totals = {p: item_tot[p] + svc.get(p, 0) + gst.get(p, 0) for p in people}
    print(json.dumps({"allocations": allocations, "charges": charges, "totals": totals}))


main()
