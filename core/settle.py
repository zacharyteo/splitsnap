"""Trip settlement: net balances across outings -> minimal-ish transfer list (greedy)."""
from __future__ import annotations


def net_balances(outings: list[dict]) -> dict[str, int]:
    """outing = {"payer": str, "totals": {person: cents}}. Positive = is owed money."""
    net: dict[str, int] = {}
    for o in outings:
        for p, c in o["totals"].items():
            net[p] = net.get(p, 0) - c
        net[o["payer"]] = net.get(o["payer"], 0) + sum(o["totals"].values())
    return net


def naive_ious(outings: list[dict]) -> int:
    """How many transfers people would make without simplification."""
    return sum(1 for o in outings for p, c in o["totals"].items() if p != o["payer"] and c > 0)


def settle(net: dict[str, int]) -> list[dict]:
    cred = sorted([[p, c] for p, c in net.items() if c > 0], key=lambda x: -x[1])
    debt = sorted([[p, -c] for p, c in net.items() if c < 0], key=lambda x: -x[1])
    out, i, j = [], 0, 0
    while i < len(debt) and j < len(cred):
        amt = min(debt[i][1], cred[j][1])
        out.append({"from": debt[i][0], "to": cred[j][0], "cents": amt})
        debt[i][1] -= amt
        cred[j][1] -= amt
        if debt[i][1] == 0:
            i += 1
        if cred[j][1] == 0:
            j += 1
    return out
