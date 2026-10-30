"""
Turn report.json into one self-contained web page the owner (or a judge) can open in any browser.

No internet needed: CSS and the little bit of JavaScript (copy buttons, filter) are inline.
"""

from __future__ import annotations

import html

CLASS_LABEL = {
    "rate_mismatch": "Rate mismatch",
    "duplicate_bill": "Duplicate bill",
    "scheme_not_given": "Scheme not given",
    "paid_but_shown_due": "Paid but shown due",
    "gst_error": "GST error",
    "credit_period_risk": "Credit-period risk",
}
STATUS_LABEL = {"confirmed": "Problem found", "conflict": "Owner decides", "cannot_determine": "Document missing"}
ACTION_LABEL = {
    "draft_whatsapp_reply": "Draft ready to send",
    "send_whatsapp_reply": "Sent automatically",
    "remind_owner": "Reminder for the owner",
    "escalate_to_owner": "Needs the owner's decision",
    "ask_owner": "Question for the owner",
}


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _inr(x: float) -> str:
    x = round(float(x or 0), 2)
    return f"₹{x:,.0f}" if abs(x - round(x)) < 0.005 else f"₹{x:,.2f}"


CSS = """
:root{--bg:#f6f5f2;--card:#fff;--ink:#1d1f22;--muted:#5d636b;--line:#e3e1dc;--accent:#0b7a53;
--warn:#b45309;--bad:#b42318;--info:#1d4ed8;--chip:#f0efeb}
@media (prefers-color-scheme:dark){:root{--bg:#141517;--card:#1d1f22;--ink:#ececec;--muted:#a2a7ad;
--line:#2e3135;--accent:#3ccf91;--warn:#f0a43a;--bad:#ff6b5f;--info:#7aa2ff;--chip:#26292d}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,"Noto Sans",sans-serif}
main{max-width:1000px;margin:0 auto;padding:24px 16px 60px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 12px}
.sub{color:var(--muted);margin:0 0 20px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.stat b{display:block;font-size:24px}.stat span{color:var(--muted);font-size:13px}
.good b{color:var(--accent)}.warnc b{color:var(--warn)}
.bars .row{display:grid;grid-template-columns:170px 1fr 90px;gap:10px;align-items:center;margin:6px 0}
.bar{height:12px;border-radius:6px;background:var(--accent)}
.summary{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);
border-radius:12px;padding:14px 16px;white-space:pre-wrap}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 12px}
.filters button{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:20px;
padding:5px 12px;cursor:pointer;font:inherit;font-size:13px}.filters button.on{background:var(--ink);color:var(--bg)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:10px 0}
.top{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline;justify-content:space-between}
.title{font-weight:600}.amt{font-size:20px;font-weight:700}
.pill{display:inline-block;font-size:12px;border-radius:20px;padding:2px 9px;background:var(--chip);margin-right:6px}
.confirmed .pill.st{color:var(--accent)}.conflict .pill.st{color:var(--warn)}.cannot_determine .pill.st{color:var(--info)}
.why{margin:8px 0;color:var(--ink)}.meta{color:var(--muted);font-size:13px}
details{margin-top:8px}summary{cursor:pointer;color:var(--muted);font-size:13px}
ul.ev{margin:6px 0 0;padding-left:18px;font-size:13px;color:var(--muted);word-break:break-word}
.draft{margin-top:10px;background:var(--chip);border-radius:10px;padding:10px 12px;white-space:pre-wrap;font-size:14px}
.copy{float:right;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px;
padding:2px 10px;cursor:pointer;font:inherit;font-size:12px;margin-left:8px}
table{width:100%;border-collapse:collapse;font-size:13px;background:var(--card);border-radius:12px;overflow:hidden}
td,th{border-bottom:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}
.foot{color:var(--muted);font-size:12px;margin-top:40px}
@media (max-width:560px){.bars .row{grid-template-columns:100px 1fr 80px}h1{font-size:22px}}
"""

JS = """
function copyText(id,btn){const t=document.getElementById(id).innerText;
navigator.clipboard.writeText(t).then(()=>{btn.textContent='Copied';setTimeout(()=>btn.textContent='Copy',1500)});}
function filt(k,btn){document.querySelectorAll('.filters button').forEach(b=>b.classList.remove('on'));btn.classList.add('on');
document.querySelectorAll('.card[data-k]').forEach(c=>{c.style.display=(k==='all'||c.dataset.k.split(' ').includes(k))?'':'none'});}
"""


def render(report: dict) -> str:
    s = report["summary"]
    findings = report["findings"]
    shop = report.get("shop") or "Shop"
    open_q = s.get("conflicts", 0) + s.get("cannot_determine", 0)
    names = report.get("distributors") or {}
    problems = sum(1 for f in findings if f["status"] == "confirmed" and f["class"] != "credit_period_risk")
    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
             f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>Khata report</title><style>{CSS}</style></head><body><main>"
             f"<h1>Khata report: {_e(shop)}</h1>"
             f"<p class='sub'>Bills checked up to {_e(report.get('as_of'))} · {_e(report.get('mode', 'supervised'))} mode</p>"
             "<div class='stats'>"
             f"<div class='stat good'><b>{_inr(s['to_claim_back_inr'])}</b><span>to claim back from distributors</span></div>"
             f"<div class='stat'><b>{problems}</b><span>wrong bills found</span></div>"
             f"<div class='stat warnc'><b>{_inr(s.get('interest_at_risk_inr', 0))}</b><span>interest at risk (pay on time)</span></div>"
             f"<div class='stat'><b>{open_q}</b><span>need the owner or a document</span></div>"
             "</div>"]

    summ = report.get("owner_summary") or {}
    if summ.get("text"):
        parts.append(f"<h2>Message for the owner</h2><div class='summary' id='owner-sum'>{_e(summ['text'])}</div>"
                     f"<p class='meta'>Written by {_e(summ.get('written_by', ''))} "
                     f"<button class='copy' onclick=\"copyText('owner-sum',this)\">Copy</button></p>")

    by = s.get("by_distributor") or {}
    if by:
        top = max(by.values()) or 1
        parts.append("<h2>Money to claim back, by distributor</h2><div class='bars'>")
        for code, amt in sorted(by.items(), key=lambda kv: -kv[1]):
            parts.append(f"<div class='row'><span title='{_e(code)}'>{_e(names.get(code, code))}</span><div class='bar' style='width:{max(4, 100 * amt / top):.0f}%'></div>"
                         f"<b>{_inr(amt)}</b></div>")
        parts.append("</div>")

    parts.append("<h2>Findings</h2><div class='filters'><button class='on' onclick=\"filt('all',this)\">All</button>")
    for st in ("confirmed", "conflict", "cannot_determine"):
        if any(f["status"] == st for f in findings):
            parts.append(f"<button onclick=\"filt('{st}',this)\">{_e(STATUS_LABEL[st])}</button>")
    for code in sorted({f.get("distributor", "") for f in findings if f.get("distributor")}):
        parts.append(f"<button onclick=\"filt('d-{_e(code)}',this)\">{_e(names.get(code, code))}</button>")
    parts.append("</div>")

    if not findings:
        parts.append("<div class='card'>No problems found in these bills.</div>")
    for i, f in enumerate(findings):
        st = f["status"]
        action = f.get("action", {})
        act = action.get(report.get("mode", "supervised")) or action.get("supervised", "")
        amount = f["amount_inr"] if st == "confirmed" else f.get("amount_at_stake_inr")
        amount_html = f"<span class='amt'>{_inr(amount)}</span>" if amount else ""
        parts.append(f"<div class='card {st}' data-k='{st} d-{_e(f.get('distributor', ''))}'>"
                     f"<div class='top'><div><span class='title'>{_e(CLASS_LABEL.get(f['class'], f['class']))} · "
                     f"{_e(f['bill_id'])}</span></div>{amount_html}</div>"
                     f"<div><span class='pill st'>{_e(STATUS_LABEL.get(st, st))}</span>"
                     f"<span class='pill'>{_e(ACTION_LABEL.get(act, act))}</span>"
                     f"<span class='pill'>confidence {round(float(f.get('confidence', 0)) * 100)}%</span>"
                     + (f"<span class='pill'>{_e(f['scheme_type'].replace('_', '-'))} scheme</span>" if f.get("scheme_type") else "")
                     + "</div>"
                     f"<p class='why'>{_e(f.get('reasoning', ''))}</p>")
        if st == "conflict" and f.get("sources"):
            parts.append("<p class='meta'>" + " · ".join(f"{_e(x['source'])}: {_e(x['value'])}" for x in f["sources"]) + "</p>")
        if st == "cannot_determine":
            parts.append(f"<p class='meta'>Missing: {_e(f.get('missing'))}<br>Ask: {_e(f.get('ask'))}</p>")
        if f.get("waits_for_owner"):
            parts.append(f"<p class='meta'>Not sent automatically because: {_e(', '.join(f['waits_for_owner']).replace('_', ' '))}</p>")
        if f.get("evidence"):
            parts.append("<details><summary>Proof (" + str(len(f["evidence"])) + " lines)</summary><ul class='ev'>"
                         + "".join(f"<li>{_e(x)}</li>" for x in f["evidence"]) + "</ul></details>")
        if f.get("draft_message"):
            parts.append(f"<div class='draft'><button class='copy' onclick=\"copyText('d{i}',this)\">Copy</button>"
                         f"<span id='d{i}'>{_e(f['draft_message'])}</span></div>")
        parts.append("</div>")

    alerts = report.get("safety_alerts") or []
    if alerts:
        parts.append("<h2>Blocked messages</h2><p class='meta'>These looked like scams or tried to give orders. "
                     "Khata ignored them and did not act on them.</p><table><tr><th>Where</th><th>Why</th><th>Message</th></tr>")
        for a in alerts:
            parts.append(f"<tr><td>{_e(a.get('source'))}</td><td>{_e(a.get('reason'))}</td><td>{_e(a.get('text'))}</td></tr>")
        parts.append("</table>")

    pend = report.get("pending_requests") or []
    if pend:
        parts.append("<h2>Other open questions</h2><table><tr><th>Bill</th><th>Check</th><th>Missing</th></tr>")
        for p in pend:
            parts.append(f"<tr><td>{_e(p.get('bill_id'))}</td><td>{_e(CLASS_LABEL.get(p.get('class'), p.get('class')))}</td>"
                         f"<td>{_e(p.get('missing'))}</td></tr>")
        parts.append("</table>")

    parts.append("<p class='foot'>Khata never pays anyone and never follows instructions found inside chats, bills or "
                 "forwarded messages. Amounts are worked out from the documents; the AI only reads them.</p>"
                 f"<script>{JS}</script></main></body></html>")
    return "".join(parts)
