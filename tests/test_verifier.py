import copy
import json
import pathlib
import subprocess
import sys
import tempfile

from core.verifier import pct_round, verify_receipt, verify_split
from core.settle import net_balances, settle

ROOT = pathlib.Path(__file__).resolve().parents[1]
SAMPLES = json.loads((ROOT / "fixtures/samples.json").read_text())
REF = (ROOT / "tests/reference_split.py").read_text()

BISTRO_ASSERTIONS = [
    {"type": "excluded_from_category", "person": "Arun", "category": "drink"},
    {"type": "pays_nothing", "person": "Priya"},
    {"type": "item_shared_by", "item_id": "i1", "persons": ["Arun", "Mei", "Dan"]},
    {"type": "weight", "item_id": "i4", "weights": {"Dan": 2, "Siti": 1}},
]


def run_ref(sample, assertions):
    """Runs OUR hand-written reference script locally (never model code)."""
    inp = {"receipt": sample["receipt"], "people": sample["people"], "payer": sample["payer"],
           "rules_text": sample["rules"], "assertions": assertions}
    with tempfile.TemporaryDirectory() as d:
        pathlib.Path(d, "input.json").write_text(json.dumps(inp))
        pathlib.Path(d, "s.py").write_text(REF)
        out = subprocess.run([sys.executable, "s.py"], cwd=d, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


# ---- receipt checks (hand-computed values)
def test_sg_rates_hand_computed():
    assert pct_round(15200, 10) == 1520           # 10% SC
    assert pct_round(15200 + 1520, 9) == 1505     # 9% of 167.20 = 15.048 -> 15.05
    assert pct_round(1005, 9) == 90               # 0.9045 -> 0.90


def test_good_receipts_pass():
    assert verify_receipt(SAMPLES["bistro"]["receipt"]) == []
    assert verify_receipt(SAMPLES["hawker"]["receipt"]) == []


def test_misread_caught_by_R1():
    fails = verify_receipt(SAMPLES["misread"]["receipt"])
    assert len(fails) == 1 and fails[0].startswith("R1 FAIL") and "off by -2000" in fails[0]


def test_wrong_gst_caught_by_R2_and_R3():
    r = copy.deepcopy(SAMPLES["bistro"]["receipt"])
    r["gst_cents"] = 1672  # someone applied 10% instead of 9%
    fails = verify_receipt(r)
    assert any(f.startswith("R2") for f in fails) and any(f.startswith("R3") for f in fails)


# ---- split checks
def test_reference_split_passes_all_8():
    s = SAMPLES["bistro"]
    out = run_ref(s, BISTRO_ASSERTIONS)
    v = verify_split(s["receipt"], s["people"], BISTRO_ASSERTIONS, out)
    assert v["fails"] == [], v["fails"]
    assert len(v["passed"]) == 8
    assert out["totals"]["Priya"] == 0
    assert sum(out["totals"].values()) == 18225


def test_hawker_uneven_total_exact():
    s = SAMPLES["hawker"]
    out = run_ref(s, [])
    assert verify_split(s["receipt"], s["people"], [], out)["fails"] == []
    t = sorted(out["totals"].values())
    assert sum(t) == 3640 and t[-1] - t[0] <= 2  # 3640/3 is not whole; per-item rounding, exact total


def test_naive_equal_split_fails_S3_with_specific_message():
    s = SAMPLES["hawker"]
    each = s["receipt"]["total_cents"] // 3  # 1213 x3 = 3639, loses a cent
    out = {"allocations": [{"item_id": i["id"], "person": "Zac", "cents": i["line_cents"]} for i in s["receipt"]["items"]],
           "charges": [{"person": p, "service_cents": 0, "gst_cents": 0} for p in s["people"]],
           "totals": {p: each for p in s["people"]}}
    v = verify_split(s["receipt"], s["people"], [], out)
    assert "S3 FAIL: totals sum to 3639, receipt total is 3640 (off by -1 cents)" in v["fails"]


def test_assertion_violation_caught_S6():
    s = SAMPLES["bistro"]
    out = run_ref(s, [])  # ignores the rules -> Arun pays for drinks, Priya pays
    v = verify_split(s["receipt"], s["people"], BISTRO_ASSERTIONS, out)
    s6 = [f for f in v["fails"] if f.startswith("S6")]
    assert any("Arun pays nothing for drink" in f for f in s6)
    assert any("Priya pays nothing" in f for f in s6)
    assert "S6" not in v["passed"]


def test_garbage_output_fails_S1():
    s = SAMPLES["hawker"]
    v = verify_split(s["receipt"], s["people"], [], {"answer": 42})
    assert v["fails"][0].startswith("S1 FAIL") and v["passed"] == []


# ---- settlement
def test_settlement_reduces_transfers():
    outings = [{"payer": "A", "totals": {"A": 100, "B": 100, "C": 100}},
               {"payer": "B", "totals": {"A": 200, "B": 200, "C": 200}},
               {"payer": "C", "totals": {"A": 50, "B": 50, "C": 50}}]
    net = net_balances(outings)
    assert sum(net.values()) == 0
    tx = settle(net)
    assert len(tx) <= 2
    after = dict(net)
    for t in tx:
        after[t["from"]] += t["cents"]
        after[t["to"]] -= t["cents"]
    assert all(v == 0 for v in after.values())
