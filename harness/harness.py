"""
Khata harness CLI. Each command runs in a fresh process; everything learnt
lives under --state-dir.

    python harness/harness.py ingest   <week_dir>      --state-dir <dir>
    python harness/harness.py report   --as-of YYYY-MM-DD --mode supervised|autonomous --state-dir <dir> [-o out.json]
    python harness/harness.py feedback <feedback.json> --state-dir <dir>
    python harness/harness.py sheet    --as-of YYYY-MM-DD --state-dir <dir> [-o answer_sheet.json]

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
        log.warning("No LLAMA_API_KEY (or KHATA_OFFLINE=1): photos are read by local OCR / sidecars only, "
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
        s.set_defaults(func=fn)

    s = sub.add_parser("feedback", help="learn from the owner's answers")
    s.add_argument("feedback_file")
    s.add_argument("--state-dir", default="state")
    s.set_defaults(func=cmd_feedback)

    a = p.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
