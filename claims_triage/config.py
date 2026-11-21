"""Runtime configuration from environment."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_env_file(path: Path | None = None) -> None:
    """Load KEY=value from a local .env without overriding real env vars."""
    env_path = path or (PROJECT_ROOT / ".env")
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and value:
            os.environ.setdefault(key, value)


load_env_file()
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "claims.db"
CORPUS_VERSION = "1.0.0"
CORPUS_SEED = 42
PINNED_CORPUS_HASH = "corpus-v1.0.0-seed42"
FRESHNESS_MAX_AGE_HOURS = 24
RETRIEVAL_CONFIDENCE_THRESHOLD = 0.45
UNGROUNDED_RATE_THRESHOLD = 0.05
AUTO_ALLOW_RATE_THRESHOLD = 0.80
FABRICATION_STREAK_PAUSE = 2
BLOCK_STREAK_PAUSE = 3
UNGROUNDED_WINDOW = 10
AUTO_ALLOW_WINDOW = 20
DAILY_SPEND_CAP_USD = 1.00
DEFAULT_MODEL = "claude-haiku-4-5"
# SkillGate-style scanner: 8 reused PI rules + claims-domain rules (reported in README).
SCANNER_RULE_COUNT = 18


def db_path() -> Path:
    raw = os.environ.get("CLAIMS_DB_PATH")
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else PROJECT_ROOT / p
    return DEFAULT_DB_PATH


def corpus_version() -> str:
    return os.environ.get("CLAIMS_CORPUS_VERSION", CORPUS_VERSION)


def pinned_corpus_hash() -> str:
    return os.environ.get("CLAIMS_PINNED_CORPUS_HASH", PINNED_CORPUS_HASH)


def mode() -> str:
    return os.environ.get("CLAIMS_MODE", "stub").lower()


def model_name() -> str:
    return os.environ.get("CLAIMS_MODEL", DEFAULT_MODEL)


def daily_spend_cap_usd() -> float:
    return float(os.environ.get("CLAIMS_DAILY_SPEND_CAP_USD", str(DAILY_SPEND_CAP_USD)))


def anthropic_api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or None
