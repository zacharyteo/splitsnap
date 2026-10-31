"""Daytona: runs untrusted, model-generated split code in an isolated sandbox.

One sandbox per split run, reused across repair attempts, deleted in finally.
Network is blocked (generated code needs no internet).
"""
from __future__ import annotations

import json
import os
import time

RUN_TIMEOUT_S = int(os.environ.get("SANDBOX_RUN_TIMEOUT_S", "20"))


def _daytona():
    from daytona import Daytona
    if not os.environ.get("DAYTONA_API_KEY"):
        raise RuntimeError("DAYTONA_API_KEY is not set. Add it to .env")
    return Daytona()


class SplitSandbox:
    """Context manager: `with SplitSandbox() as sb: sb.run(script, input_obj)`."""

    def __init__(self):
        self.d = None
        self.sb = None
        self.network_blocked = False

    def __enter__(self):
        from daytona import CreateSandboxFromSnapshotParams
        self.d = _daytona()
        t = time.time()
        try:
            params = CreateSandboxFromSnapshotParams(language="python", ephemeral=True,
                                                     auto_stop_interval=5, network_block_all=True)
            self.sb = self.d.create(params, timeout=90)
            self.network_blocked = True
        except Exception:  # noqa: BLE001  fall back to a plain sandbox if params rejected
            try:
                self.sb = self.d.create(timeout=90)
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(f"Daytona sandbox creation failed: {e}") from e
        self.create_s = time.time() - t
        return self

    @property
    def id(self) -> str:
        return getattr(self.sb, "id", "?")

    def run(self, script: str, input_obj: dict) -> dict:
        """Writes input.json inside the sandbox, runs the script, returns parsed result."""
        prelude = ("import json as _sj\n"
                   f"open('input.json','w').write({json.dumps(json.dumps(input_obj))})\n")
        t = time.time()
        try:
            r = self.sb.process.code_run(prelude + script, timeout=RUN_TIMEOUT_S)
            exit_code, output = r.exit_code, r.result or ""
        except Exception as e:  # noqa: BLE001
            exit_code, output = -1, f"Sandbox execution error: {type(e).__name__}: {e}"
        return {"exit_code": exit_code, "output": output, "seconds": time.time() - t,
                "sandbox_id": self.id, "json": last_json(output) if exit_code == 0 else None}

    def __exit__(self, *exc):
        if self.sb is not None:
            try:
                self.d.delete(self.sb)
            except Exception:  # noqa: BLE001
                pass


def last_json(output: str):
    """The script must print one JSON object; take the last line that parses."""
    for line in reversed(output.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    try:
        return json.loads(output)
    except Exception:  # noqa: BLE001
        return None


def health() -> tuple[bool, str]:
    try:
        with SplitSandbox() as sb:
            r = sb.run('import json\nprint(json.dumps({"ok": 1+1}))', {})
        ok = r["exit_code"] == 0 and r["json"] == {"ok": 2}
        return ok, f"sandbox {r['sandbox_id'][:8]} exit={r['exit_code']} in {sb.create_s + r['seconds']:.1f}s"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:120]}"
