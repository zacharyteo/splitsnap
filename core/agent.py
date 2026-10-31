"""Loop 2: generate code (Nosana) -> run in Daytona -> fixed verifier -> repair (max N).

The verifier runs locally on the returned JSON, outside the sandbox, so the
generated code can never tamper with it. Every step is appended to `trace`.
"""
from __future__ import annotations

import os
import time
from typing import Callable

from . import codegen
from .verifier import verify_split

MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "3"))


def run_split(receipt, people, payer, rules_text, assertions,
              on_event: Callable[[dict], None] = lambda e: None,
              sandbox_factory=None, gen=codegen.generate, fix=codegen.repair) -> dict:
    """Returns {"status": "PASS"|"FAIL", "output", "verification", "trace", "script"}."""
    if sandbox_factory is None:
        from .sandbox import SplitSandbox
        sandbox_factory = SplitSandbox
    trace: list[dict] = []

    def log(**e):
        e["t"] = time.strftime("%H:%M:%S")
        trace.append(e)
        on_event(e)

    log(step="codegen", status="running", detail="Nosana LLM writing split code")
    script, model, secs = gen(receipt, people, payer, rules_text, assertions)
    log(step="codegen", status="done", model=model, seconds=round(secs, 1), script=script)

    result = {"status": "FAIL", "output": None, "verification": None, "trace": trace, "script": script}
    log(step="sandbox", status="running", detail="Creating Daytona sandbox")
    with sandbox_factory() as sb:
        log(step="sandbox", status="done", sandbox_id=sb.id, network_blocked=getattr(sb, "network_blocked", False),
            seconds=round(getattr(sb, "create_s", 0), 1))
        for attempt in range(1, MAX_ATTEMPTS + 1):
            r = sb.run(script, {"receipt": receipt, "people": people, "payer": payer,
                                "rules_text": rules_text, "assertions": assertions})
            tail = r["output"][-1500:]
            if r["exit_code"] != 0:
                ver = {"fails": [f"RUNTIME FAIL: exit code {r['exit_code']}: {tail[-600:]}"], "passed": [], "total": 8}
            elif r["json"] is None:
                ver = {"fails": ["S1 FAIL: no JSON object found on stdout; print exactly one JSON line"],
                       "passed": [], "total": 8}
            else:
                ver = verify_split(receipt, people, assertions, r["json"])
            ok = not ver["fails"]
            log(step="attempt", attempt=attempt, status="pass" if ok else "fail", sandbox_id=r["sandbox_id"],
                exit_code=r["exit_code"], seconds=round(r["seconds"], 1), passed=ver["passed"],
                total_checks=ver["total"], fails=ver["fails"], script=script, output_tail=tail)
            result.update(output=r["json"], verification=ver, script=script)
            if ok:
                result["status"] = "PASS"
                return result
            if attempt == MAX_ATTEMPTS:
                break
            log(step="repair", status="running", attempt=attempt, detail="Sending verifier failures back to Nosana LLM")
            script, model, secs = fix(script, ver["fails"], tail, receipt, people, payer, rules_text, assertions)
            log(step="repair", status="done", attempt=attempt, model=model, seconds=round(secs, 1))
    return result
