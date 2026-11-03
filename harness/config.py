"""
Settings. The API key only ever comes from the environment or the .env file, never from code.
Every MODEL_* setting also works under its older LLAMA_* name.

    MODEL_API_KEY          Meta Model API key. Also read as LLAMA_API_KEY
    MODEL_BASE_URL         https://api.meta.ai/v1 by default, or any OpenAI-style host serving Llama
    MODEL_VISION_MODEL     muse-spark-1.3 by default (bill photos, UPI screenshots, statements)
    MODEL_TEXT_MODEL       muse-spark-1.3 by default (messages, PDFs, feedback)
    MODEL_VOICE_MODEL      muse-voice-transcribe-1.0 by default (voice notes, Meta Model API only)
    MODEL_REASONING_EFFORT low by default (minimal / low / medium / high)
    WHISPER_MODEL          local Whisper size, used if Muse Voice Transcribe isn't available
    KHATA_OFFLINE=1        don't call any model
    KHATA_IGNORE_SIDECARS=1  ignore the saved .json/.txt readings and read the real files
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
        if value.lower() in ("paste-your-key-here", "your-key-here", "your-key"):
            continue  # placeholder, not a real key
        if key and value and not os.environ.get(key):
            os.environ[key] = value


_load_dotenv()


def _env(*names: str, default: str = "") -> str:
    for n in names:
        v = os.environ.get(n)
        if v and v.strip():
            return v.strip()
    return default


META_MODEL_API = "https://api.meta.ai/v1"
LLAMA_API = "https://api.llama.com/compat/v1"


@dataclass
class LlamaConfig:
    """Model endpoint. Default: Meta Model API with Muse Spark (the hackathon's API).

    Set MODEL_BASE_URL to another OpenAI-compatible host serving open Llama weights (e.g. a local
    Ollama at http://localhost:11434/v1) and the model ids it uses to run on Llama instead.
    """
    api_key: str = ""
    base_url: str = META_MODEL_API
    text_model: str = "muse-spark-1.3"
    vision_model: str = "muse-spark-1.3"
    voice_model: str = "muse-voice-transcribe-1.0"   # Meta Model API speech-to-text
    reasoning_effort: str = "low"                    # Muse Spark: minimal | low | medium | high
    timeout: int = 120
    max_retries: int = 3
    offline: bool = False

    @property
    def is_meta_model_api(self) -> bool:
        return "api.meta.ai" in self.base_url

    @classmethod
    def from_env(cls) -> "LlamaConfig":
        base = _env("MODEL_BASE_URL", "LLAMA_BASE_URL", default=META_MODEL_API).rstrip("/")
        meta = "api.meta.ai" in base
        default_model = "muse-spark-1.3" if meta else "Llama-4-Maverick-17B-128E-Instruct-FP8"
        return cls(
            api_key=_env("MODEL_API_KEY", "LLAMA_API_KEY"),
            base_url=base,
            text_model=_env("MODEL_TEXT_MODEL", "LLAMA_TEXT_MODEL", default=default_model),
            vision_model=_env("MODEL_VISION_MODEL", "LLAMA_VISION_MODEL", default=default_model),
            voice_model=_env("MODEL_VOICE_MODEL", default=cls.voice_model),
            reasoning_effort=_env("MODEL_REASONING_EFFORT", default=cls.reasoning_effort).lower(),
            timeout=int(_env("MODEL_TIMEOUT", "LLAMA_TIMEOUT", default="120")),
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
