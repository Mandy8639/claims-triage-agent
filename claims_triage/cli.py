"""CLI: claims seed | eval | serve | review | mcp."""

from __future__ import annotations

import json
from pathlib import Path

import typer
import uvicorn
from rich import print as rprint

from claims_triage.config import db_path
from claims_triage.corpus.seed import refresh_updated_at, seed_corpus
from claims_triage.corpus.store import ClaimStore
from claims_triage.review.queue import apply_gate_decision, pending_reviews

app = typer.Typer(help="Bounded claims triage agent (synthetic demo).")
review_app = typer.Typer(help="Human review queue")
app.add_typer(review_app, name="review")


@app.command()
def seed(force: bool = typer.Option(False, "--force")) -> None:
    """Seed the synthetic corpus (seed=42)."""
    path = seed_corpus(force=force)
    refresh_updated_at(path)
    rprint(f"[green]Seeded[/green] {path}")


@app.command("serve")
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the FastAPI dashboard."""
    seed_corpus(force=False)
    refresh_updated_at()
    uvicorn.run("claims_triage.api.app:app", host=host, port=port, reload=False)


@app.command()
def eval(
    mode: str = typer.Option("stub", "--mode"),
    write: bool = typer.Option(True, "--write/--no-write"),
) -> None:
    """Run the frozen eval harness."""
    from evals.run_evals import main as eval_main

    eval_main(["--mode", mode] + (["--write"] if write else []))


@review_app.command("list")
def review_list() -> None:
    store = ClaimStore(db_path())
    rows = pending_reviews(store)
    rprint(json.dumps(rows, indent=2, default=str))


@review_app.command("decide")
def review_decide(
    run_id: str,
    gate: str = typer.Option(..., "--gate"),
    decision: str = typer.Option(..., "--decision"),
    reason: str = typer.Option(..., "--reason"),
    actor: str = typer.Option("adjuster", "--actor"),
) -> None:
    store = ClaimStore(db_path())
    updated = apply_gate_decision(
        store, run_id, gate=gate, decision=decision, actor=actor, reason=reason
    )
    rprint(json.dumps(updated, indent=2, default=str))


@app.command()
def mcp() -> None:
    """Run the MCP server over stdio."""
    from claims_triage.mcp_server import main as mcp_main

    mcp_main()


@app.command("run-packet")
def run_packet_cmd(
    packet_id: str,
    auto_gates: bool = typer.Option(False, "--auto-gates"),
    gate1: str | None = typer.Option(None, "--gate1"),
    gate2: str | None = typer.Option(None, "--gate2"),
) -> None:
    from claims_triage.agent.loop import run_packet

    seed_corpus(force=False)
    refresh_updated_at()
    store = ClaimStore(db_path())
    result = run_packet(
        store,
        packet_id,
        auto_gates=auto_gates,
        gate1_decision=gate1,
        gate2_decision=gate2,
    )
    Path("data").mkdir(exist_ok=True)
    rprint(
        {
            "run_id": result.get("run_id"),
            "status": result.get("status"),
            "guardrail_verdict": result.get("guardrail_verdict"),
            "reason_code": result.get("reason_code"),
            "abstained": result.get("abstained"),
            "trace_id": result.get("trace_id"),
        }
    )


if __name__ == "__main__":
    app()
