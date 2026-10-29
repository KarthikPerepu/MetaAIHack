"""
Khata Harness Configuration.

Reads API keys and base URLs from environment variables.
Provides centralized config for all modules.
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LlamaConfig:
    """Configuration for Llama API access."""
    api_key: str = ""
    base_url: str = "https://api.llama.com/v1"
    text_model: str = "llama-3.3-70b-instruct"
    vision_model: str = "llama-3.2-90b-vision-instruct"
    temperature: float = 0.1
    max_tokens: int = 4096
    timeout: int = 120

    @classmethod
    def from_env(cls) -> "LlamaConfig":
        """Load configuration from environment variables."""
        return cls(
            api_key=(os.environ.get("LLAMA_API_KEY") or os.environ.get("MODEL_API_KEY", "")).strip(),
            base_url=(os.environ.get("LLAMA_BASE_URL") or "https://api.llama.com/v1").strip().rstrip("/"),
            text_model=(os.environ.get("LLAMA_TEXT_MODEL") or "llama-3.3-70b-instruct").strip(),
            vision_model=(os.environ.get("LLAMA_VISION_MODEL") or "llama-3.2-90b-vision-instruct").strip(),
        )


@dataclass
class WhisperConfig:
    """Configuration for speech-to-text."""
    model_size: str = "base"
    language: str = "hi"  # Hindi / Hinglish
    device: str = "cpu"

    @classmethod
    def from_env(cls) -> "WhisperConfig":
        return cls(
            model_size=os.environ.get("WHISPER_MODEL", "base"),
            device=os.environ.get("WHISPER_DEVICE", "cpu"),
        )


@dataclass
class HarnessConfig:
    """Top-level harness configuration."""
    state_dir: str = "state"
    llama: LlamaConfig = field(default_factory=LlamaConfig.from_env)
    whisper: WhisperConfig = field(default_factory=WhisperConfig.from_env)

    # Safety thresholds
    autonomous_amount_limit: float = 2000.0
    escalation_cap: float = 0.60  # Max 60% of findings escalated
    confidence_threshold: float = 0.70

    # Duplicate detection
    duplicate_date_tolerance_days: int = 2
    duplicate_similarity_threshold: float = 0.85

    # Credit period defaults
    default_credit_days: int = 30
    default_interest_rate: float = 0.18  # 18% p.a.
    credit_risk_window_days: int = 7

    # GST reference: HSN prefix → rate
    gst_rates: dict = field(default_factory=lambda: {
        "0401": 0.05, "0402": 0.05, "0403": 0.05, "0404": 0.05, "0405": 0.05, "0406": 0.05,
        "1704": 0.18,
        "1905": 0.18,  # Default; ≤₹100/kg → 0.05
        "2106": 0.18,
        "3401": 0.18,
        "3402": 0.18,
        "2201": 0.05,
        "2202": 0.28,
    })

    @classmethod
    def load(cls) -> "HarnessConfig":
        return cls(
            llama=LlamaConfig.from_env(),
            whisper=WhisperConfig.from_env(),
        )
