"""Exercises the repair loop offline: fake LLM + a LOCAL runner standing in for Daytona.
Only our own test scripts run locally here; in the app every script runs in Daytona."""
import json
import pathlib
import subprocess
import sys
import tempfile

from core.agent import run_split
from core.sandbox import last_json
from tests.test_verifier import BISTRO_ASSERTIONS, REF, SAMPLES

BUGGY = """import json
d = json.load(open('input.json'))
r, ppl = d['receipt'], d['people']
each = r['total_cents'] // len(ppl)   # naive: loses cents, ignores rules
print(json.dumps({"allocations": [], "charges": [], "totals": {p: each for p in ppl}}))
"""


class LocalRunner:
    id = "local-test"
    create_s = 0.0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def run(self, script, inp):
        with tempfile.TemporaryDirectory() as d:
            pathlib.Path(d, "input.json").write_text(json.dumps(inp))
            pathlib.Path(d, "s.py").write_text(script)
            p = subprocess.run([sys.executable, "s.py"], cwd=d, capture_output=True, text=True, timeout=20)
        out = p.stdout + p.stderr
        return {"exit_code": p.returncode, "output": out, "seconds": 0.01, "sandbox_id": self.id,
                "json": last_json(out) if p.returncode == 0 else None}


def test_loop_fails_then_repairs_then_passes():
    s = SAMPLES["bistro"]
    seen_fails = []

    def gen(*a):
        return BUGGY, "fake-model", 0.0

    def fix(script, fails, tail, *a):
        seen_fails.extend(fails)
        return REF, "fake-model", 0.0

    res = run_split(s["receipt"], s["people"], s["payer"], s["rules"], BISTRO_ASSERTIONS,
                    sandbox_factory=LocalRunner, gen=gen, fix=fix)
    attempts = [e for e in res["trace"] if e["step"] == "attempt"]
    assert res["status"] == "PASS"
    assert [a["status"] for a in attempts] == ["fail", "pass"]
    assert any(f.startswith("S2 FAIL") for f in seen_fails)  # repair got real verifier messages
    assert any(f.startswith("S6 FAIL") for f in seen_fails)


def test_runtime_error_is_fed_back():
    s = SAMPLES["hawker"]
    calls = []

    def gen(*a):
        return "raise ValueError('boom')\n", "fake", 0.0

    def fix(script, fails, *a):
        calls.append(fails)
        return REF, "fake", 0.0

    res = run_split(s["receipt"], s["people"], s["payer"], s["rules"], [],
                    sandbox_factory=LocalRunner, gen=gen, fix=fix)
    assert res["status"] == "PASS"
    assert calls[0][0].startswith("RUNTIME FAIL") and "boom" in calls[0][0]


def test_gives_up_honestly_after_max_attempts():
    s = SAMPLES["hawker"]
    res = run_split(s["receipt"], s["people"], s["payer"], s["rules"], [], sandbox_factory=LocalRunner,
                    gen=lambda *a: (BUGGY, "f", 0), fix=lambda *a: (BUGGY, "f", 0))
    assert res["status"] == "FAIL"
    assert len([e for e in res["trace"] if e["step"] == "attempt"]) == 3
