"""
Settings. The API key only ever comes from the environment or the .env file, never from code.
Every MODEL_* setting also works under its older LLAMA_* name.

    MODEL_PROVIDER         meta (default: Muse on the Meta Model API) or ollama (open Llama on this computer)
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
OLLAMA_API = "http://localhost:11434/v1"
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
        # MODEL_PROVIDER picks sensible defaults: "meta" (Muse Spark on the Meta Model API, the default)
        # or "ollama" (open Llama weights on this computer, no key needed). Any setting below still wins.
        provider = _env("MODEL_PROVIDER", default="meta").lower()
        if provider == "ollama":
            preset_url, preset_model, preset_key = OLLAMA_API, "llama3.2-vision", "ollama"
        else:
            preset_url, preset_model, preset_key = META_MODEL_API, "", ""
        base = _env("MODEL_BASE_URL", "LLAMA_BASE_URL", default=preset_url).rstrip("/")
        meta = "api.meta.ai" in base
        default_model = preset_model or ("muse-spark-1.3" if meta else "Llama-4-Maverick-17B-128E-Instruct-FP8")
        return cls(
            api_key=_env("MODEL_API_KEY", "LLAMA_API_KEY", default=preset_key),
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
    """The rules' numbers and choices. Where the brief is open, our reading is the default and the
    alternatives are one line in .env away (see PROBLEM.md):

    KHATA_INTEREST_DAYS        to_window_end (default) | one_day | full_window
    KHATA_SCHEME_SCALING       by_wording (default: "10 pe 1" scales, "20 lo 2 free" doesn't) | always | never
    KHATA_DUPLICATE_WINDOW_DAYS  2
    KHATA_VOICE_ONLY_AUTOSEND  no (default) | yes
    KHATA_AUTOSEND_LIMIT       2000          KHATA_AUTOSEND_MIN_CONFIDENCE  0.75
    KHATA_RATE_TOLERANCE       0.5           KHATA_CREDIT_DAYS  30   KHATA_INTEREST_RATE  0.18
    """
    autonomous_amount_limit: float = 2000.0      # send only when amount < ₹2,000
    autosend_min_confidence: float = 0.75
    escalation_cap: float = 0.60                 # never escalate more than 60%
    escalation_target: float = 0.50              # stay comfortably below the cap
    default_credit_days: int = 30
    default_interest_rate: float = 0.18          # 18% a year
    credit_window_days: int = 7
    interest_days: str = "to_window_end"         # how many days of interest "would be due"
    duplicate_window_days: int = 2
    scheme_scaling: str = "by_wording"           # does "X pe Y free" scale with quantity
    voice_only_autosend: bool = False            # may a claim resting only on a voice note go out alone
    rate_tolerance: float = 0.5                  # ₹ per unit ignored as rounding
    tax_tolerance: float = 1.0                   # ₹ per bill ignored as rounding

    CHOICES = {"interest_days": ("to_window_end", "one_day", "full_window"),
               "scheme_scaling": ("by_wording", "always", "never")}

    @classmethod
    def from_env(cls) -> "Policy":
        import logging
        log = logging.getLogger("khata.config")
        p = cls()

        def num(name, attr, cast):
            raw = _env(name)
            if raw:
                try:
                    setattr(p, attr, cast(raw))
                except ValueError:
                    log.warning(f"{name}={raw!r} isn't a number; keeping {getattr(p, attr)}")

        def choice(name, attr):
            raw = _env(name).lower()
            if raw:
                if raw in cls.CHOICES[attr]:
                    setattr(p, attr, raw)
                else:
                    log.warning(f"{name}={raw!r} isn't one of {', '.join(cls.CHOICES[attr])}; keeping {getattr(p, attr)}")

        choice("KHATA_INTEREST_DAYS", "interest_days")
        choice("KHATA_SCHEME_SCALING", "scheme_scaling")
        num("KHATA_DUPLICATE_WINDOW_DAYS", "duplicate_window_days", int)
        num("KHATA_AUTOSEND_LIMIT", "autonomous_amount_limit", float)
        num("KHATA_AUTOSEND_MIN_CONFIDENCE", "autosend_min_confidence", float)
        num("KHATA_RATE_TOLERANCE", "rate_tolerance", float)
        num("KHATA_CREDIT_DAYS", "default_credit_days", int)
        num("KHATA_INTEREST_RATE", "default_interest_rate", float)
        if p.default_interest_rate > 1:          # someone wrote 18 meaning 18%
            p.default_interest_rate /= 100.0
        p.voice_only_autosend = _env("KHATA_VOICE_ONLY_AUTOSEND").lower() in ("1", "yes", "true")
        return p

    def rules(self) -> dict:
        """The choices this run used, printed in every report so a reader knows our reading of the brief."""
        return {"interest_days": self.interest_days, "scheme_scaling": self.scheme_scaling,
                "duplicate_window_days": self.duplicate_window_days,
                "voice_only_autosend": self.voice_only_autosend,
                "autosend_limit_inr": self.autonomous_amount_limit,
                "autosend_min_confidence": self.autosend_min_confidence,
                "default_credit_days": self.default_credit_days,
                "default_interest_rate": self.default_interest_rate,
                "credit_window_days": self.credit_window_days,
                "rate_tolerance_inr": self.rate_tolerance,
                "escalation_cap": self.escalation_cap}


def load_gst_table() -> dict:
    with open(DATA_DIR / "gst_rates.json", "r", encoding="utf-8") as f:
        return json.load(f)


@dataclass
class HarnessConfig:
    state_dir: str = "state"
    llama: LlamaConfig = field(default_factory=LlamaConfig.from_env)
    policy: Policy = field(default_factory=Policy.from_env)
    whisper_model: str = field(default_factory=lambda: _env("WHISPER_MODEL", default="small"))
    ignore_sidecars: bool = field(default_factory=lambda: _env("KHATA_IGNORE_SIDECARS") in ("1", "true", "yes"))
    gst: dict = field(default_factory=load_gst_table)
