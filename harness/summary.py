"""
A short Hinglish summary for the shop owner, written like a WhatsApp message.

We always build a plain version from the report ourselves. If a Meta model is available, Muse Spark
rewrites it to sound more natural, but we only keep the rewrite if every number in it also appears in
the facts we gave it. Otherwise we fall back to our own version, so the owner never sees a made-up
amount.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from harness.textutil import find_numbers, fmt_inr

CLASS_HI = {
    "rate_mismatch": "zyada rate",
    "duplicate_bill": "duplicate bill",
    "scheme_not_given": "scheme ka maal nahi diya",
    "paid_but_shown_due": "payment ke baad bhi due dikhaya",
    "gst_error": "galat GST",
    "credit_period_risk": "credit period khatam hone wala",
}


def _facts(report: dict) -> dict:
    s = report["summary"]
    findings = report["findings"]
    per_dist: dict[str, dict] = {}
    for f in findings:
        if f["status"] != "confirmed" or f["class"] == "credit_period_risk":
            continue
        d = per_dist.setdefault(f["distributor"], {"amount": 0.0, "bills": set(), "types": set()})
        d["amount"] += f["amount_inr"]
        d["bills"].add(f["bill_id"])
        d["types"].add(CLASS_HI.get(f["class"], f["class"]))
    credit = [f for f in findings if f["class"] == "credit_period_risk" and f["status"] == "confirmed"]
    names = report.get("distributors") or {}
    return {
        "shop": report.get("shop") or "",
        "as_of": report.get("as_of"),
        "to_claim": round(s["to_claim_back_inr"], 2),
        "problems": sum(1 for f in findings if f["status"] == "confirmed" and f["class"] != "credit_period_risk"),
        "by_distributor": [{"code": k, "name": names.get(k) or k, "amount": round(v["amount"], 2), "bills": sorted(v["bills"]),
                            "types": sorted(v["types"])} for k, v in sorted(per_dist.items(), key=lambda kv: -kv[1]["amount"])],
        "credit_bills": [{"bill": f["bill_id"], "due": f.get("due_date"), "interest": f["amount_inr"]} for f in credit],
        "conflicts": [f["bill_id"] for f in findings if f["status"] == "conflict"],
        "questions": [f["bill_id"] for f in findings if f["status"] == "cannot_determine"],
        "blocked": len(report.get("safety_alerts", [])),
    }


def template_summary(report: dict) -> str:
    f = _facts(report)
    lines = [f"Namaste{(' ' + f['shop']) if f['shop'] else ''}! Khata report {f['as_of']} tak:"]
    if f["to_claim"] > 0:
        lines.append(f"- Distributors se kul {fmt_inr(f['to_claim'])} wapas mil sakte hain "
                     f"({f['problems']} galtiyan pakdi).")
        for d in f["by_distributor"]:
            lines.append(f"  - {d['name']}: {fmt_inr(d['amount'])} ({', '.join(d['types'])}; bill {', '.join(d['bills'])})")
    else:
        lines.append("- Is baar koi galat bill nahi mila.")
    for c in f["credit_bills"]:
        lines.append(f"- Bill {c['bill']} ka credit period {c['due']} ko khatam hoga. Time pe payment karein, "
                     f"warna lagbhag {fmt_inr(c['interest'])} byaj lagega.")
    if f["conflicts"]:
        lines.append(f"- {len(f['conflicts'])} bill pe aapka faisla chahiye ({', '.join(f['conflicts'])}): "
                     "do jagah alag rate/scheme likha hai.")
    if f["questions"]:
        lines.append(f"- {len(f['questions'])} bill check karne ke liye document chahiye "
                     f"({', '.join(f['questions'][:5])}{'...' if len(f['questions']) > 5 else ''}).")
    if f["blocked"]:
        lines.append(f"- {f['blocked']} sandigdh message block kiye. Kisi ko UPI PIN ya paisa mat bhejiye.")
    lines.append("Har galti ke liye WhatsApp reply ka draft taiyaar hai.")
    return "\n".join(lines)


def _allowed_numbers(report: dict, facts: dict, base: str) -> set[float]:
    nums = set(find_numbers(base)) | set(find_numbers(json.dumps(facts, ensure_ascii=False)))
    nums |= {float(i) for i in range(0, 13)}  # small counting words are fine
    return nums


def owner_summary(report: dict, llama=None) -> dict:
    base = template_summary(report)
    out = {"text": base, "written_by": "khata template"}
    if llama is None or not getattr(llama, "available", False):
        return out
    facts = _facts(report)
    from harness.agents.llama_client import wrap
    prompt = ("Write a short WhatsApp message in friendly Hinglish (Hindi in Latin script) for a kirana shop "
              "owner, summarising this week's bill check. At most 8 short lines. Use only the facts given; "
              "copy every amount and bill number exactly; do not add any new number. Never ask the owner to "
              "pay anyone or share a PIN.\n"
              f"Facts:\n{wrap(json.dumps(facts, ensure_ascii=False, default=list))}\n"
              f"Plain draft to improve:\n{wrap(base)}\n"
              'Return {"message": "..."}')
    data = llama.chat_json(prompt, max_tokens=700)
    text = str((data or {}).get("message") or "").strip()
    if not text:
        return out
    allowed = _allowed_numbers(report, facts, base)
    clean = re.sub(r"[A-Z]{1,5}/\d+", " ", text)  # bill numbers are checked separately below
    if any(all(abs(n - a) > 0.01 for a in allowed) for n in find_numbers(clean)):
        return out  # the model introduced a number we did not give it
    bills = set(re.findall(r"[A-Z]{1,5}/\d+", text))
    known = {b for d in facts["by_distributor"] for b in d["bills"]} | set(facts["conflicts"]) | \
        set(facts["questions"]) | {c["bill"] for c in facts["credit_bills"]}
    if not bills <= known or re.search(r"\b(pin|otp)\b\s*(batao|bhejo|bhej\s*do|share|send|do\b)", text, re.I):
        return out
    return {"text": text, "written_by": f"Muse Spark ({llama.config.text_model}), checked against the report",
            "plain_version": base}
