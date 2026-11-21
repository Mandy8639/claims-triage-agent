"""Deterministic validation + abstention."""

from __future__ import annotations

from pathlib import Path

import pytest
from claims_triage.corpus.seed import refresh_updated_at, seed_corpus
from claims_triage.corpus.store import ClaimStore
from claims_triage.tools.validate import validate_packet


@pytest.fixture()
def store(tmp_path: Path) -> ClaimStore:
    db = tmp_path / "claims.db"
    seed_corpus(db, force=True)
    refresh_updated_at(db)
    return ClaimStore(db)


def test_clean_packet_passes(store: ClaimStore) -> None:
    report = validate_packet(store, "FNOL-0001")
    assert report["overall"] == "pass"
    assert report["abstain"] is False


def test_missing_certificate_abstains(store: ClaimStore) -> None:
    report = validate_packet(store, "FNOL-0007")
    assert report["abstain"] is True
    assert report["abstain_reason"] == "missing_death_certificate"


def test_conflict_abstains(store: ClaimStore) -> None:
    report = validate_packet(store, "FNOL-0014")
    assert report["abstain"] is True
    assert report["abstain_reason"] == "conflicting_beneficiary_claims"
