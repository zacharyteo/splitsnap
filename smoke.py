"""Run first: python smoke.py  -> checks Daytona and the Nosana endpoint end to end."""
from dotenv import load_dotenv

load_dotenv()

from core import llm, sandbox  # noqa: E402

ok_d, msg_d = sandbox.health()
print(f"DAYTONA {'OK  ' if ok_d else 'FAIL'} {msg_d}")
ok_n, msg_n = llm.health()
print(f"NOSANA  {'OK  ' if ok_n else 'FAIL'} models: {msg_n}")
if ok_n:
    text, model, secs = llm.chat([{"role": "user", "content": "Reply with just: pong"}], max_tokens=5)
    print(f"LLM     {model} -> {text.strip()!r} in {secs:.1f}s")
