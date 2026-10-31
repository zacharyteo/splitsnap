"""SplitSnap: receipt + plain-English rules -> split verified to the cent.
Run: streamlit run app.py
"""
import json
import pathlib
import uuid

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from core import agent, extract, ledger, llm, rules, sandbox  # noqa: E402
from core.settle import naive_ious, net_balances, settle  # noqa: E402
from core.verifier import RECEIPT_CHECKS, SPLIT_CHECKS, verify_receipt  # noqa: E402

st.set_page_config(page_title="SplitSnap", page_icon="🧾", layout="wide")
SAMPLES = json.loads((pathlib.Path(__file__).parent / "fixtures/samples.json").read_text())
ss = st.session_state
ss.setdefault("outings", [])
ss.setdefault("trip_id", f"trip-{uuid.uuid4().hex[:8]}")  # one Neo4j Group per trip


def money(c):
    return f"${c / 100:,.2f}"


def load_sample(key):
    s = SAMPLES[key]
    ss.receipt = json.loads(json.dumps(s["receipt"]))
    ss.people_text = ", ".join(s["people"])
    ss.payer = s["payer"]
    ss.rules_text = s["rules"]
    for k in ("interp", "assertions", "confirmed", "result", "extract_trace"):
        ss.pop(k, None)
    ss.editor_ver = ss.get("editor_ver", 0) + 1


if "receipt" not in ss:
    load_sample("bistro")

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title("🧾 SplitSnap")
    st.caption("Plain-English rules → code written on **Nosana** → run in a **Daytona** sandbox → "
               "checked by a fixed verifier → auto-repaired.")
    st.subheader("Load a sample")
    pick = st.selectbox("Sample receipt (TEST DATA)", list(SAMPLES), format_func=lambda k: SAMPLES[k]["label"])
    if st.button("Load sample", width="stretch"):
        load_sample(pick)
        st.rerun()
    st.subheader("Sponsor status")
    if st.button("Check connections", width="stretch"):
        with st.spinner("Pinging Daytona and Nosana…"):
            ss.health = {"Daytona": sandbox.health(), "Nosana": llm.health()}
    for name, (ok, msg) in ss.get("health", {}).items():
        (st.success if ok else st.error)(f"**{name}** {'OK' if ok else 'DOWN'}: {msg}")
    st.caption(f"Neo4j ledger: {'on' if ledger.enabled() else 'off (optional)'}")
    st.caption(f"Vision model: {llm.vision_model() or 'none set (use samples / edit table)'}")

tab_split, tab_trip = st.tabs(["Split a bill", f"Settle the trip ({len(ss.outings)} outings)"])

# ================================================================== SPLIT
with tab_split:
    # ---------------- 1. receipt
    st.header("1 · Receipt")
    with st.expander("📷 Read a receipt photo (Nosana vision model)", expanded=False):
        img = st.file_uploader("Receipt photo", type=["jpg", "jpeg", "png"])
        if img and st.button("Read receipt"):
            try:
                with st.spinner("Nosana vision model reading the receipt (Loop 1 checks the sums)…"):
                    res = extract.extract(img.getvalue(), img.type or "image/jpeg")
                ss.extract_trace = res["trace"]
                if res["receipt"]:
                    ss.receipt = res["receipt"]
                    ss.editor_ver = ss.get("editor_ver", 0) + 1
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(str(e))
        for e in ss.get("extract_trace", []):
            icon = "✅" if e["status"] == "pass" else "🔁"
            st.write(f"{icon} Read {e['read']} by `{e['model']}` in {e['seconds']}s")
            for f in e["fails"]:
                st.caption(f)

    r = ss.receipt
    st.caption(f"**{r.get('merchant', 'Receipt')}** · edit anything the photo got wrong")
    df = pd.DataFrame([{"id": i["id"], "item": i["name"], "qty": i.get("qty", 1),
                        "line $": i["line_cents"] / 100, "category": i.get("category", "food")} for i in r["items"]])
    edited = st.data_editor(df, num_rows="dynamic", width="stretch", key=f"items_{ss.get('editor_ver', 0)}",
                            column_config={"category": st.column_config.SelectboxColumn(
                                options=["food", "drink", "dessert", "other"]),
                                "line $": st.column_config.NumberColumn(format="%.2f")})
    c1, c2, c3, c4 = st.columns(4)
    sub = c1.number_input("Subtotal $", value=r["subtotal_cents"] / 100, step=0.01, format="%.2f")
    sc = c2.number_input("Service charge $", value=r.get("service_charge_cents", 0) / 100, step=0.01, format="%.2f")
    gst = c3.number_input("GST $", value=r.get("gst_cents", 0) / 100, step=0.01, format="%.2f")
    tot = c4.number_input("Total $", value=r["total_cents"] / 100, step=0.01, format="%.2f")

    items = []
    for n, row in enumerate(edited.to_dict("records"), 1):
        if not row.get("item") or pd.isna(row.get("line $")):
            continue
        items.append({"id": row["id"] if isinstance(row.get("id"), str) and row["id"] else f"i{n}",
                      "name": str(row["item"]), "qty": int(row.get("qty") or 1),
                      "line_cents": int(round(float(row["line $"]) * 100)),
                      "category": row.get("category") or "food"})
    receipt = {**r, "items": items, "subtotal_cents": int(round(sub * 100)), "discount_cents": 0,
               "service_charge_cents": int(round(sc * 100)), "gst_cents": int(round(gst * 100)),
               "total_cents": int(round(tot * 100))}
    r_fails = verify_receipt(receipt)
    failed = {f[:2] for f in r_fails}
    st.markdown("**Loop 1 · receipt checks** " + " ".join(
        f"{'✅' if cid not in failed else '❌'} {cid}" for cid in RECEIPT_CHECKS))
    for f in r_fails:
        st.error(f)
    if not r_fails:
        st.caption(" · ".join(f"{k}: {v}" for k, v in RECEIPT_CHECKS.items()))

    # ---------------- 2. people
    st.header("2 · People")
    c1, c2 = st.columns([3, 1])
    people_text = c1.text_input("Names (comma-separated)", key="people_text")
    people = [p.strip() for p in people_text.split(",") if p.strip()]
    payer = c2.selectbox("Who paid?", people, index=people.index(ss.payer) if ss.get("payer") in people else 0)

    # ---------------- 3. rules
    st.header("3 · Rules in plain English")
    rules_text = st.text_area("How should it be split?", key="rules_text", height=80)
    if st.button("Interpret rules (Nosana)", disabled=bool(r_fails) or not people):
        try:
            with st.spinner("Nosana LLM turning rules into checkable assertions…"):
                ss.interp = rules.interpret(rules_text, receipt, people)
            ss.assertions_text = json.dumps(ss.interp["assertions"], indent=1)
            ss.confirmed = False
        except Exception as e:  # noqa: BLE001
            st.error(f"Rule interpretation failed: {e}")
    if "interp" in ss:
        it = ss.interp
        st.markdown(f"**I understood** (by `{it['model']}`, {it['seconds']:.1f}s):")
        for a in it["assertions"]:
            st.markdown(f"- `{a['type']}` " + ", ".join(f"{k}={v}" for k, v in a.items() if k != "type"))
        if not it["assertions"]:
            st.markdown("- split everything equally")
        for q in it["questions"]:
            st.warning(f"❓ {q}")
        for u in it["uncertain_items"]:
            st.info(f"Unsure about {u.get('item_id')}: {u.get('why')}")
        for d in it["dropped"]:
            st.caption(f"Ignored invalid assertion: {d}")
        with st.expander("Edit assertions (JSON)"):
            st.text_area("assertions", key="assertions_text", height=160, label_visibility="collapsed")
        if st.button("✅ Confirm these rules", type="primary"):
            try:
                raw = json.loads(ss.assertions_text or "[]")
                ss.assertions, dropped = rules.validate(raw, people, [i["id"] for i in items])
                ss.confirmed = True
                for d in dropped:
                    st.warning(f"Dropped: {d}")
            except json.JSONDecodeError as e:
                st.error(f"Assertions JSON is invalid: {e}")
        if ss.get("confirmed"):
            st.success(f"Confirmed {len(ss.assertions)} rule(s). The verifier will enforce them.")

    # ---------------- 4. run
    st.header("4 · Split (Daytona + repair loop)")
    can_run = not r_fails and people and ss.get("confirmed")
    if not ss.get("confirmed"):
        st.caption("Interpret and confirm the rules first.")
    trace_box = st.container()

    def render_trace(trace, box):
        box.empty()
        with box.container():
            for e in trace:
                if e["step"] == "codegen" and e["status"] == "done":
                    st.markdown(f"🧠 **Code written** by `{e['model']}` on Nosana ({e['seconds']}s)")
                elif e["step"] == "sandbox" and e["status"] == "done":
                    st.markdown(f"📦 **Daytona sandbox** `{e['sandbox_id']}` ready ({e['seconds']}s)"
                                f"{' · network blocked' if e.get('network_blocked') else ''}")
                elif e["step"] == "attempt":
                    with st.container(border=True):
                        ok = e["status"] == "pass"
                        st.markdown(f"{'🟢' if ok else '🔴'} **Attempt {e['attempt']}** · sandbox `{e['sandbox_id']}` · "
                                    f"exit {e['exit_code']} · {e['seconds']}s · "
                                    f"**{len(e['passed'])}/{e['total_checks']} checks passed**")
                        for f in e["fails"]:
                            st.code(f, language=None)
                        with st.expander("Code that ran in the sandbox"):
                            st.code(e["script"], language="python")
                elif e["step"] == "repair" and e["status"] == "running" and e is trace[-1]:
                    st.markdown(f"🔧 **Repair {e['attempt']}**: verifier failures sent back to Nosana LLM…")
                elif e["step"] == "repair" and e["status"] == "done":
                    st.markdown(f"🔧 Repaired by `{e['model']}` ({e['seconds']}s) → re-running in Daytona")
                elif e["status"] == "running" and e is trace[-1]:
                    st.markdown(f"⏳ {e.get('detail', e['step'])}…")

    if st.button("▶ Run split", type="primary", disabled=not can_run):
        live = []
        box = trace_box.empty()

        def on_event(e):
            live.append(e)
            render_trace(live, box)

        try:
            ss.result = agent.run_split(receipt, people, payer, rules_text, ss.assertions, on_event=on_event)
            ss.result["receipt"], ss.result["people"], ss.result["payer"] = receipt, people, payer
        except Exception as e:  # noqa: BLE001
            st.error(f"Run stopped: {e}")
    elif "result" in ss:
        render_trace(ss.result["trace"], trace_box.empty())

    # ---------------- 5. result
    res = ss.get("result")
    if res:
        st.header("5 · Result")
        v = res["verification"] or {"passed": [], "total": len(SPLIT_CHECKS), "fails": ["no output"]}
        if res["status"] == "PASS":
            st.success(f"✓ Verified: {len(v['passed'])}/{v['total']} independent checks passed. "
                       f"Shares add up to {money(res['receipt']['total_cents'])} exactly.")
        else:
            st.error(f"Not verified after {agent.MAX_ATTEMPTS} attempts. Showing the honest trace above; "
                     "try rephrasing the rules.")
        st.caption(" · ".join(f"{'✅' if c in v['passed'] else '❌'} {c} {d}" for c, d in SPLIT_CHECKS.items()))
        out = res["output"]
        if res["status"] == "PASS" and out:
            items_by = {}
            for a in out["allocations"]:
                items_by[a["person"]] = items_by.get(a["person"], 0) + a["cents"]
            ch = {c["person"]: c for c in out["charges"]}
            rows = [{"person": p, "items": money(items_by.get(p, 0)),
                     "service": money(ch.get(p, {}).get("service_cents", 0)),
                     "GST": money(ch.get(p, {}).get("gst_cents", 0)), "owes": money(out["totals"][p]),
                     "": "paid" if p == res["payer"] else ""} for p in res["people"]]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            lines = [f"🧾 {res['receipt'].get('merchant', 'Dinner')}: {money(res['receipt']['total_cents'])} "
                     f"(paid by {res['payer']})"]
            lines += [f"• {p}: {money(out['totals'][p])}" for p in res["people"]
                      if p != res["payer"] and out["totals"][p] > 0]
            lines.append(f"Pay {res['payer']} back 🙏 (verified to the cent by SplitSnap)")
            st.text_area("WhatsApp message", "\n".join(lines), height=150)
            c1, c2 = st.columns(2)
            c1.download_button("Download split.json", json.dumps(
                {"receipt": res["receipt"], "assertions": ss.assertions, "split": out,
                 "verification": v, "trace": [{k: e[k] for k in e if k != "script"} for e in res["trace"]]},
                indent=1), "split.json", "application/json")
            if c2.button("➕ Add to trip"):
                ss.outings.append({"venue": res["receipt"].get("merchant", "Outing"), "payer": res["payer"],
                                   "totals": dict(out["totals"])})
                if ledger.enabled():
                    try:
                        ledger.save_outing(ss.trip_id, ss.outings[-1]["venue"], res["payer"], out["totals"])
                    except Exception as e:  # noqa: BLE001
                        st.warning(f"Neo4j save failed (kept locally): {e}")
                st.success("Added to trip.")

# ================================================================== TRIP
with tab_trip:
    st.header("Settle the whole trip")
    if not ss.outings:
        st.info("Run a split and click **Add to trip**. Add 2–3 outings with different payers.")
    else:
        for o in ss.outings:
            st.markdown(f"- **{o['venue']}**, paid by {o['payer']}: {money(sum(o['totals'].values()))}")
        net = None
        if ledger.enabled():
            try:
                net = ledger.net_balances(ss.trip_id)
                st.caption(f"Net balances computed by one Cypher query in Neo4j (Group `{ss.trip_id}`).")
            except Exception as e:  # noqa: BLE001
                st.warning(f"Neo4j unavailable, computing locally: {e}")
        net = net or net_balances(ss.outings)
        st.dataframe(pd.DataFrame([{"person": p, "net": money(c),
                                    "status": "is owed" if c > 0 else ("owes" if c < 0 else "settled")}
                                   for p, c in sorted(net.items(), key=lambda x: -x[1])]),
                     hide_index=True, width="stretch")
        tx = settle(net)
        st.subheader(f"{naive_ious(ss.outings)} IOUs → {len(tx)} transfers")
        for t in tx:
            st.markdown(f"💸 **{t['from']}** pays **{t['to']}** {money(t['cents'])}")
        if st.button("Clear trip"):
            ss.outings = []
            ss.trip_id = f"trip-{uuid.uuid4().hex[:8]}"
            st.rerun()
