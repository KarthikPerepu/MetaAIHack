"""
Configuration. Secrets come only from the environment, never from files in the repo.

    LLAMA_API_KEY        key for an OpenAI-compatible endpoint serving Meta Llama models
                         (MODEL_API_KEY is accepted as an alias)
    LLAMA_BASE_URL       e.g. https://api.llama.com/compat/v1  (Meta Llama API, default)
                              https://api.together.xyz/v1       (Together)
                              https://api.groq.com/openai/v1    (Groq)
                              http://localhost:11434/v1         (Ollama, local)
    LLAMA_TEXT_MODEL     text reasoning model id
    LLAMA_VISION_MODEL   vision model id (bill photos, UPI screenshots, statements)
    WHISPER_MODEL        faster-whisper / openai-whisper size for voice notes (default: small)
    KHATA_OFFLINE=1      never call a model; use deterministic parsers only
    KHATA_IGNORE_SIDECARS=1  ignore <file>.json / <file>.txt sidecars (forces real extraction)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"


def _load_dotenv() -> None:
    """Read KEY=value lines from a private .env file in the project root (git-ignored).
    Values already set in the environment win."""
    path = DATA_DIR.parent.parent / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and value and not os.environ.get(key):
            os.environ[key] = value


_load_dotenv()


def _env(*names: str, default: str = "") -> str:
    for n in names:
        v = os.environ.get(n)
        if v and v.strip():
            return v.strip()
    return default


@dataclass
class LlamaConfig:
    api_key: str = ""
    base_url: str = "https://api.llama.com/compat/v1"
    text_model: str = "Llama-4-Maverick-17B-128E-Instruct-FP8"
    vision_model: str = "Llama-4-Maverick-17B-128E-Instruct-FP8"
    timeout: int = 90
    max_retries: int = 3
    offline: bool = False

    @classmethod
    def from_env(cls) -> "LlamaConfig":
        return cls(
            api_key=_env("LLAMA_API_KEY", "MODEL_API_KEY"),
            base_url=_env("LLAMA_BASE_URL", default=cls.base_url).rstrip("/"),
            text_model=_env("LLAMA_TEXT_MODEL", default=cls.text_model),
            vision_model=_env("LLAMA_VISION_MODEL", default=cls.vision_model),
            timeout=int(_env("LLAMA_TIMEOUT", default="90")),
            offline=_env("KHATA_OFFLINE") in ("1", "true", "yes"),
        )


@dataclass
class Policy:
    """Numbers that come from the problem statement / glossary."""
    autonomous_amount_limit: float = 2000.0      # send only when amount < ₹2,000
    escalation_cap: float = 0.60                 # never escalate more than 60%
    escalation_target: float = 0.50              # stay comfortably below the cap
    default_credit_days: int = 30
    default_interest_rate: float = 0.18          # 18% p.a.
    credit_window_days: int = 7
    duplicate_window_days: int = 2
    rate_tolerance: float = 0.5                  # ₹ per unit ignored as rounding
    tax_tolerance: float = 1.0                   # ₹ per bill ignored as rounding


def load_gst_table() -> dict:
    with open(DATA_DIR / "gst_rates.json", "r", encoding="utf-8") as f:
        return json.load(f)


@dataclass
class HarnessConfig:
    state_dir: str = "state"
    llama: LlamaConfig = field(default_factory=LlamaConfig.from_env)
    policy: Policy = field(default_factory=Policy)
    whisper_model: str = field(default_factory=lambda: _env("WHISPER_MODEL", default="small"))
    ignore_sidecars: bool = field(default_factory=lambda: _env("KHATA_IGNORE_SIDECARS") in ("1", "true", "yes"))
    gst: dict = field(default_factory=load_gst_table)
