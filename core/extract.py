"""Loop 1: receipt photo -> ReceiptJSON via a Nosana-served vision model, checked by verify_receipt.

Only active when NOSANA_VISION_MODEL is set. Otherwise the app uses sample
receipts / the editable items table (stated honestly in the README).
"""
from __future__ import annotations

import base64

from . import llm
from .verifier import verify_receipt

MAX_REREADS = 2

PROMPT = """Read this receipt. Return ONLY a JSON object:
{"merchant": str, "items": [{"id": "i1", "name": str, "qty": int, "line_cents": int,
  "category": "food|drink|dessert|other", "category_confidence": 0-1}],
 "subtotal_cents": int, "discount_cents": int, "service_charge_cents": int, "gst_cents": int,
 "total_cents": int, "notes": [str]}
All money in integer cents (e.g. $12.50 -> 1250). line_cents is the line total (qty x unit price).
Use 0 for service charge or GST if not printed. Number item ids i1, i2, ... Do not invent items."""


def extract(image_bytes: bytes, mime: str = "image/jpeg", on_event=lambda e: None) -> dict:
    model = llm.vision_model()
    if not model:
        raise RuntimeError("No vision model configured (NOSANA_VISION_MODEL). Use a sample or edit the table.")
    url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
    hint, receipt, fails, trace = "", None, [], []
    for k in range(1, MAX_REREADS + 2):
        content = [{"type": "text", "text": PROMPT + hint}, {"type": "image_url", "image_url": {"url": url}}]
        text, _, secs = llm.chat([{"role": "user", "content": content}], model=model, max_tokens=2000)
        try:
            receipt = llm.parse_json(text)
            fails = verify_receipt(receipt)
        except Exception as e:  # noqa: BLE001
            fails = [f"PARSE FAIL: {e}"]
        e = {"step": "extract", "read": k, "model": model, "seconds": round(secs, 1),
             "status": "pass" if not fails else "fail", "fails": fails}
        trace.append(e)
        on_event(e)
        if not fails:
            break
        hint = ("\n\nYour previous read failed these checks:\n" + "\n".join(fails) +
                "\nRe-read carefully, especially quantities, multi-line items and the totals block.")
    return {"receipt": receipt, "fails": fails, "trace": trace}
