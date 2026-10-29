"""
Khata Harness CLI.

Main entry point for:
1. ingest:   Process a week directory of WhatsApp exports into state
2. report:   Run detectors and output findings report JSON
3. feedback: Incorporate shop owner feedback into knowledge state
"""

from __future__ import annotations
import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

# Ensure workspace root is first in sys.path, NOT harness directory
_root = str(Path(__file__).resolve().parent.parent)
_harness_dir = str(Path(__file__).resolve().parent)
if sys.path and sys.path[0] == _harness_dir:
    sys.path.pop(0)
if _root not in sys.path:
    sys.path.insert(0, _root)

# Reconfigure stdout/stderr to UTF-8 for cross-platform rupee symbol (\u20b9) support
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Configure default logging to sys.stderr so stdout stays clean for JSON output
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("khata.harness")


def get_components(state_dir: str):
    """Instantiate config, store, llama client, pipeline, and report generator."""
    from harness.config import HarnessConfig
    from harness.agents.llama_client import LlamaClient
    from harness.knowledge.store import KnowledgeStore

    config = HarnessConfig(state_dir=state_dir)
    store = KnowledgeStore(state_dir=state_dir)
    llama = LlamaClient(config.llama)
    return config, store, llama


def cmd_ingest(args):
    """Handle 'ingest <week_dir> --state-dir <dir>'."""
    from harness.ingest.pipeline import IngestPipeline

    config, store, _ = get_components(args.state_dir)
    pipeline = IngestPipeline(config, store)

    logger.info(f"Ingesting week exports from: {args.week_dir}")
    stats = pipeline.ingest(args.week_dir)
    print(json.dumps(stats, indent=2))


def cmd_report(args):
    """Handle 'report --as-of YYYY-MM-DD --mode supervised --state-dir <dir>'."""
    from harness.report import ReportGenerator

    config, store, llama = get_components(args.state_dir)
    as_of = date.fromisoformat(args.as_of)
    mode = args.mode.lower()

    generator = ReportGenerator(config, store, llama)
    report = generator.generate(as_of=as_of, mode=mode)

    report_str = json.dumps(report, indent=2, ensure_ascii=False)
    if getattr(args, "output", None):
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report_str)
        logger.info(f"Report written to {args.output}")
    else:
        # Output pure JSON to stdout (critical for '>' shell redirection)
        print(report_str)


def cmd_feedback(args):
    """Handle 'feedback <feedback.json> --state-dir <dir>'."""
    from harness.feedback import FeedbackLearner

    config, store, llama = get_components(args.state_dir)
    learner = FeedbackLearner(config, store, llama)

    logger.info(f"Processing feedback file: {args.feedback_file}")
    result = learner.process_feedback_file(args.feedback_file)
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(
        prog="khata",
        description="Khata Harness: AI-powered bill discrepancy detection for Kirana shops",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. Ingest command
    p_ingest = subparsers.add_parser("ingest", help="Ingest a week of WhatsApp exports")
    p_ingest.add_argument("week_dir", type=str, help="Directory containing week's exports")
    p_ingest.add_argument(
        "--state-dir",
        type=str,
        default="state",
        help="Directory to persist knowledge state (default: state)",
    )
    p_ingest.set_defaults(func=cmd_ingest)

    # 2. Report command
    p_report = subparsers.add_parser("report", help="Generate findings report")
    p_report.add_argument(
        "--as-of",
        type=str,
        required=True,
        help="Report cut-off date in YYYY-MM-DD format",
    )
    p_report.add_argument(
        "--mode",
        type=str,
        choices=["supervised", "autonomous"],
        default="supervised",
        help="Execution mode (supervised or autonomous, default: supervised)",
    )
    p_report.add_argument(
        "--state-dir",
        type=str,
        default="state",
        help="Directory where knowledge state is persisted",
    )
    p_report.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Optional file path to write UTF-8 report JSON directly",
    )
    p_report.set_defaults(func=cmd_report)

    # 3. Feedback command
    p_feedback = subparsers.add_parser("feedback", help="Incorporate owner feedback")
    p_feedback.add_argument(
        "feedback_file",
        type=str,
        help="Path to feedback JSON file",
    )
    p_feedback.add_argument(
        "--state-dir",
        type=str,
        default="state",
        help="Directory where knowledge state is persisted",
    )
    p_feedback.set_defaults(func=cmd_feedback)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
