"""Synthetic corpus counts and honesty labels."""

from __future__ import annotations

import random
from pathlib import Path

from claims_triage.config import CORPUS_SEED
from claims_triage.corpus.seed import build_corpus, refresh_updated_at, seed_corpus
from claims_triage.corpus.store import ClaimStore


def test_corpus_sizes() -> None:
    data = build_corpus(random.Random(CORPUS_SEED))
    assert len(data["policies"]) == 40
    assert len(data["clauses"]) == 25
    assert len(data["templates"]) == 15
    assert len(data["packets"]) == 20


def test_seed_meta(tmp_path: Path) -> None:
    db = tmp_path / "claims.db"
    seed_corpus(db, force=True)
    refresh_updated_at(db)
    store = ClaimStore(db)
    assert store.meta("synthetic") == "true"
    assert store.meta("seed") == "42"
    assert len(store.list_policy_ids()) == 40
