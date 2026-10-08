from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


TRUE_VALUES = {"1", "true", "yes", "on"}


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in TRUE_VALUES


@dataclass(frozen=True, slots=True)
class Settings:
    provider: str
    model: str | None
    api_key: str | None
    allow_ai: bool
    local_accept_threshold: float
    human_review_threshold: float

    @classmethod
    def load(
        cls,
        env_file: Path | None = None,
        *,
        provider_override: str | None = None,
        model_override: str | None = None,
        allow_ai_override: bool | None = None,
    ) -> "Settings":
        load_dotenv(dotenv_path=env_file, override=False)
        allow_ai = (
            _bool_env("ALLOW_AI", False)
            if allow_ai_override is None
            else allow_ai_override
        )
        return cls(
            provider=provider_override or os.getenv("AI_PROVIDER", "openrouter"),
            model=model_override or os.getenv("AI_MODEL") or None,
            api_key=os.getenv("OPENROUTER_API_KEY") or None,
            allow_ai=allow_ai,
            local_accept_threshold=float(os.getenv("LOCAL_ACCEPT_THRESHOLD", "0.90")),
            human_review_threshold=float(os.getenv("HUMAN_REVIEW_THRESHOLD", "0.85")),
        )

    def validate_for_ai(self) -> None:
        if self.provider.lower() != "openrouter":
            raise ValueError(f"Unsupported AI provider: {self.provider}")
        if not self.model:
            raise ValueError("AI_MODEL is required when AI is enabled")
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY is required when AI is enabled")
        if not 0 <= self.human_review_threshold <= self.local_accept_threshold <= 1:
            raise ValueError("Confidence thresholds must satisfy 0 <= review <= local <= 1")
