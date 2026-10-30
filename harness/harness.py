"""
Khata harness CLI. Each command runs in a fresh process; everything learnt
lives under --state-dir.

    python harness/harness.py ingest   <week_dir>      --state-dir <dir>
    python harness/harness.py report   --as-of YYYY-MM-DD --mode supervised|autonomous --state-dir <dir> [-o out.json]
    python harness/harness.py feedback <feedback.json> --state-dir <dir>
    python harness/harness.py sheet    --as-of YYYY-MM-DD --state-dir <dir> [-o answer_sheet.json]
    python harness/harness.py check    <week_dir> [--as-of YYYY-MM-DD] [--out folder]   (ingest + report + web page)

JSON goes to stdout (or -o); logs go to stderr.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
_HERE = str(Path(__file__).resolve().parent)
if sys.path and sys.path[0] == _HERE:
    sys.path.pop(0)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

logging.basicConfig(level=os.environ.get("KHATA_LOG", "INFO"), stream=sys.stderr,
                    format="%(levelname)s %(name)s: %(message)s")
for noisy in ("httpx", "openai", "pdfminer", "PIL", "faster_whisper"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("khata")


def _components(state_dir: str):
    from harness.agents.llama_client import LlamaClient
    from harness.config import HarnessConfig
    from harness.knowledge.store import KnowledgeStore
    config = HarnessConfig(state_dir=state_dir)
    store = KnowledgeStore(state_dir)
    llama = LlamaClient(config.llama, cache_dir=store.cache_dir)
    if not llama.available:
        log.warning("No model key (MODEL_API_KEY / LLAMA_API_KEY) or KHATA_OFFLINE=1: photos are read by local OCR / sidecars only, "
                    "and messages by the deterministic parser.")
    return config, store, llama


def _emit(obj, out: str | None) -> None:
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    if out:
        Path(out).write_text(text + "\n", encoding="utf-8")
        log.info(f"wrote {out}")
    else:
        print(text)


def cmd_ingest(a) -> int:
    from harness.ingest.pipeline import IngestPipeline
    config, store, llama = _components(a.state_dir)
    stats = IngestPipeline(config, store, llama).ingest(a.week_dir)
    _emit(stats, None)
    return 0


def cmd_report(a) -> int:
    from harness.report import ReportGenerator
    config, store, llama = _components(a.state_dir)
    rep = ReportGenerator(config, store, llama).generate(date.fromisoformat(a.as_of), a.mode)
    _emit(rep, a.output)
    if getattr(a, "html", None):
        _write_html(rep, a.html)
    return 0


def _write_html(rep: dict, path: str) -> None:
    from harness.html_report import render
    Path(path).write_text(render(rep), encoding="utf-8")
    log.info(f"wrote {path} (open it in a browser)")


def cmd_check(a) -> int:
    """Read one WhatsApp export folder and write report.json + report.html in one go."""
    from harness.ingest.pipeline import IngestPipeline
    from harness.report import ReportGenerator
    config, store, llama = _components(a.state_dir)
    stats = IngestPipeline(config, store, llama).ingest(a.week_dir)
    as_of = date.fromisoformat(a.as_of) if a.as_of else (
        date.fromisoformat(stats["last_message_date"]) if stats.get("last_message_date") else date.today())
    rep = ReportGenerator(config, store, llama).generate(as_of, a.mode)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    _emit(rep, str(out / "report.json"))
    _write_html(rep, str(out / "report.html"))
    s = rep["summary"]
    print(f"Checked {stats.get('bills', 0)} bills up to {as_of}: {s['confirmed']} problems, "
          f"{s['to_claim_back_inr']:,.0f} rupees to claim back, "
          f"{s['conflicts'] + s['cannot_determine']} open questions, {len(rep['safety_alerts'])} messages blocked.")
    print(f"Open {out / 'report.html'} in your browser.")
    return 0


def cmd_sheet(a) -> int:
    from harness.report import ReportGenerator, answer_sheet
    config, store, llama = _components(a.state_dir)
    rep = ReportGenerator(config, store, llama).generate(date.fromisoformat(a.as_of), a.mode)
    _emit(answer_sheet(rep), a.output)
    return 0


def cmd_feedback(a) -> int:
    from harness.feedback import FeedbackLearner
    config, store, llama = _components(a.state_dir)
    _emit(FeedbackLearner(config, store, llama).process_feedback_file(a.feedback_file), None)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="khata", description="Find the money leaking out of a shop's WhatsApp.")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("ingest", help="ingest one week of WhatsApp exports")
    s.add_argument("week_dir")
    s.add_argument("--state-dir", default="state")
    s.set_defaults(func=cmd_ingest)

    for name, fn, helptext in (("report", cmd_report, "write the findings report"),
                               ("sheet", cmd_sheet, "write the per-class answer sheet")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("--as-of", required=True)
        s.add_argument("--mode", choices=["supervised", "autonomous"], default="supervised")
        s.add_argument("--state-dir", default="state")
        s.add_argument("-o", "--output", default=None)
        if name == "report":
            s.add_argument("--html", default=None, help="also write a web page version of the report")
        s.set_defaults(func=fn)

    s = sub.add_parser("check", help="read one WhatsApp export folder and write report.json + report.html")
    s.add_argument("week_dir")
    s.add_argument("--as-of", default=None, help="default: date of the last message in the chat")
    s.add_argument("--mode", choices=["supervised", "autonomous"], default="supervised")
    s.add_argument("--state-dir", default="state/check")
    s.add_argument("--out", default="khata_report", help="folder for report.json and report.html")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("feedback", help="learn from the owner's answers")
    s.add_argument("feedback_file")
    s.add_argument("--state-dir", default="state")
    s.set_defaults(func=cmd_feedback)

    a = p.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
