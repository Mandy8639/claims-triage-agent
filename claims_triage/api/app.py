"""FastAPI demo API + static dashboard."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from claims_triage.agent.loop import run_packet
from claims_triage.config import PROJECT_ROOT, SCANNER_RULE_COUNT, db_path
from claims_triage.corpus.seed import refresh_updated_at, seed_corpus
from claims_triage.corpus.store import ClaimStore
from claims_triage.review.queue import apply_gate_decision, pending_reviews

STATIC_DIR = PROJECT_ROOT / "static"
REPORTS_DIR = PROJECT_ROOT / "evals" / "reports"


def get_store() -> ClaimStore:
    path = db_path()
    if not path.exists():
        seed_corpus(path, force=True)
        refresh_updated_at(path)
    return ClaimStore(path)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    path = db_path()
    seed_corpus(path, force=False)
    refresh_updated_at(path)
    yield


app = FastAPI(
    title="claims-triage-agent",
    description=(
        "Bounded claims triage & drafting demo over SYNTHETIC data. "
        "Not affiliated with New York Life. Never approves, denies, or pays a claim."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


class RunBody(BaseModel):
    packet_id: str
    auto_gates: bool = False
    gate1_decision: str | None = None
    gate2_decision: str | None = None
    actor: str = "adjuster"


class GateBody(BaseModel):
    gate: str = Field(..., pattern="^(gate1|gate2)$")
    decision: str = Field(..., pattern="^(approve|reject|edit)$")
    reason: str
    actor: str = "adjuster"
    edited_draft: str | None = None


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "synthetic": True,
        "affiliation": "none — not affiliated with New York Life",
        "scanner_rule_count": SCANNER_RULE_COUNT,
    }


@app.get("/api/packets")
def list_packets() -> list[dict[str, Any]]:
    store = get_store()
    packets = store.list_packets()
    # Redact DOB in list view
    out = []
    for p in packets:
        out.append(
            {
                "id": p["id"],
                "policy_id": p["policy_id"],
                "scenario_tags": p.get("scenario_tags"),
                "death_certificate_present": p.get("death_certificate_present"),
                "beneficiary_id_complete": p.get("beneficiary_id_complete"),
                "conflicting_beneficiary_claims": p.get("conflicting_beneficiary_claims"),
                "injected_pattern": p.get("injected_pattern"),
            }
        )
    return out


@app.get("/api/packets/{packet_id}")
def get_packet(packet_id: str) -> dict[str, Any]:
    p = get_store().get_packet(packet_id)
    if p is None:
        raise HTTPException(404, "packet not found")
    return p


@app.post("/api/run")
def api_run(body: RunBody) -> dict[str, Any]:
    store = get_store()
    if store.get_packet(body.packet_id) is None:
        raise HTTPException(404, "packet not found")
    return run_packet(
        store,
        body.packet_id,
        auto_gates=body.auto_gates,
        gate1_decision=body.gate1_decision,
        gate2_decision=body.gate2_decision,
        actor=body.actor,
    )


@app.get("/api/runs")
def api_runs(limit: int = 50) -> list[dict[str, Any]]:
    return get_store().list_runs(limit=limit)


@app.get("/api/runs/{run_id}")
def api_run_get(run_id: str) -> dict[str, Any]:
    run = get_store().get_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return run


@app.get("/api/review")
def api_review() -> list[dict[str, Any]]:
    return pending_reviews(get_store())


@app.post("/api/runs/{run_id}/gate")
def api_gate(run_id: str, body: GateBody) -> dict[str, Any]:
    try:
        return apply_gate_decision(
            get_store(),
            run_id,
            gate=body.gate,
            decision=body.decision,
            actor=body.actor,
            reason=body.reason,
            edited_draft=body.edited_draft,
        )
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/evals/latest")
def evals_latest(mode: str = "stub") -> dict[str, Any]:
    path = REPORTS_DIR / f"latest-{mode}.json"
    if not path.exists():
        candidates = sorted(REPORTS_DIR.glob(f"*-{mode}.json"))
        if not candidates:
            raise HTTPException(404, "no eval report committed")
        path = candidates[-1]
    return json.loads(path.read_text())


@app.get("/api/evals/latest.html", response_class=HTMLResponse)
def evals_latest_html(mode: str = "stub") -> str:
    path = REPORTS_DIR / f"latest-{mode}.html"
    if not path.exists():
        raise HTTPException(404, "no HTML eval report")
    return path.read_text()


@app.get("/")
def dashboard() -> FileResponse:
    index = STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(404, "dashboard not built yet")
    return FileResponse(index)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
