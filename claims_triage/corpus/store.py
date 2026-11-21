"""Governed read/write access to the synthetic SQLite store."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claims_triage.config import FRESHNESS_MAX_AGE_HOURS, PINNED_CORPUS_HASH, db_path
from claims_triage.corpus.schema import connect, init_schema


@dataclass
class Freshness:
    ok: bool
    reason: str
    corpus_hash: str


class ClaimStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or db_path()
        conn = connect(self.path)
        init_schema(conn)
        conn.close()

    def _conn(self):
        return connect(self.path)

    def meta(self, key: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return None if row is None else row["value"]

    def corpus_hash(self) -> str:
        return self.meta("corpus_hash") or ""

    def freshness_check(self) -> Freshness:
        h = self.corpus_hash()
        if h != PINNED_CORPUS_HASH:
            return Freshness(False, f"hash mismatch: {h}", h)
        updated = self.meta("updated_at")
        if not updated:
            return Freshness(False, "missing updated_at", h)
        try:
            ts = datetime.fromisoformat(updated)
        except ValueError:
            return Freshness(False, "bad updated_at", h)
        age_h = (datetime.now(UTC) - ts).total_seconds() / 3600
        if age_h > FRESHNESS_MAX_AGE_HOURS:
            return Freshness(False, f"stale by {age_h:.1f}h", h)
        return Freshness(True, "ok", h)

    def is_paused(self) -> bool:
        return (self.meta_control("paused") or "false").lower() == "true"

    def meta_control(self, key: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM controls WHERE key = ?", (key,)).fetchone()
            return None if row is None else row["value"]

    def set_paused(self, paused: bool, reason: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO controls(key, value) VALUES('paused', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("true" if paused else "false",),
            )
            if reason:
                conn.execute(
                    "INSERT INTO controls(key, value) VALUES('pause_reason', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (reason,),
                )
            conn.commit()

    def list_policy_ids(self) -> set[str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT id FROM policies").fetchall()
        return {r["id"] for r in rows}

    def get_policy(self, policy_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT payload FROM policies WHERE id = ?", (policy_id,)).fetchone()
        return None if row is None else json.loads(row["payload"])

    def get_packet(self, packet_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT payload FROM packets WHERE id = ?", (packet_id,)).fetchone()
        return None if row is None else json.loads(row["payload"])

    def list_packets(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT payload FROM packets ORDER BY id").fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def list_clauses(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT payload FROM clauses ORDER BY id").fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def list_templates(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT payload FROM templates ORDER BY id").fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def search_corpus(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Simple keyword retrieval over clauses, policies, templates. Returns cited passages."""
        q = query.lower()
        tokens = [t for t in q.replace(",", " ").split() if len(t) >= 3]
        hits: list[tuple[float, dict[str, Any]]] = []

        def score_text(text: str) -> float:
            tl = text.lower()
            if not tokens:
                return 0.0
            return sum(1.0 for t in tokens if t in tl) / len(tokens)

        for clause in self.list_clauses():
            blob = f"{clause['kind']} {clause['text']}"
            s = score_text(blob)
            if clause["id"].lower() in q:
                s = max(s, 1.0)
            if s > 0:
                hits.append(
                    (
                        s,
                        {
                            "id": clause["id"],
                            "kind": "clause",
                            "source_label": clause["source_label"],
                            "version": clause["version"],
                            "passage": clause["text"],
                            "confidence": round(s, 3),
                        },
                    )
                )
        for pol in (self.get_policy(pid) for pid in self.list_policy_ids() if pid.lower() in q):
            if pol is None:
                continue
            hits.append(
                (
                    1.0,
                    {
                        "id": pol["id"],
                        "kind": "policy",
                        "source_label": pol["source_label"],
                        "version": pol["version"],
                        "passage": (
                            f"Policy {pol['id']} face_amount={pol['face_amount']} "
                            f"effective={pol['effective_date']} state={pol['state']} "
                            f"contestability_months={pol['contestability_months']}"
                        ),
                        "confidence": 1.0,
                    },
                )
            )
        for tpl in self.list_templates():
            blob = f"{tpl['track']} {tpl['tone_rules']} {tpl['body_skeleton']}"
            s = score_text(blob)
            if any(t in q for t in ("template", "letter", "correspondence", tpl["track"])):
                s = max(s, 0.6)
            if s > 0:
                hits.append(
                    (
                        s,
                        {
                            "id": tpl["id"],
                            "kind": "template",
                            "source_label": tpl["source_label"],
                            "version": tpl["version"],
                            "passage": (
                                f"Track={tpl['track']}. Tone: {tpl['tone_rules']}. "
                                f"Skeleton: {tpl['body_skeleton']}"
                            ),
                            "confidence": round(s, 3),
                        },
                    )
                )
        hits.sort(key=lambda x: (-x[0], x[1]["id"]))
        return [h for _, h in hits[:limit]]

    def append_run(self, run: dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO runs(
                  id, packet_id, trace_id, status, guardrail_verdict, reason_code,
                  validation_json, draft_json, gate1, gate2, had_draft, ungrounded_rate, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run["id"],
                    run.get("packet_id"),
                    run["trace_id"],
                    run["status"],
                    run.get("guardrail_verdict"),
                    run.get("reason_code"),
                    json.dumps(run.get("validation")),
                    json.dumps(run.get("draft")),
                    run.get("gate1"),
                    run.get("gate2"),
                    1 if run.get("had_draft") else 0,
                    float(run.get("ungrounded_rate") or 0),
                    run.get("created_at") or datetime.now(UTC).isoformat(),
                ),
            )
            conn.commit()

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = []
        vals: list[Any] = []
        for k, v in fields.items():
            if k in ("validation", "draft"):
                cols.append(f"{k}_json = ?")
                vals.append(json.dumps(v))
            elif k == "had_draft":
                cols.append("had_draft = ?")
                vals.append(1 if v else 0)
            else:
                cols.append(f"{k} = ?")
                vals.append(v)
        vals.append(run_id)
        with self._conn() as conn:
            conn.execute(f"UPDATE runs SET {', '.join(cols)} WHERE id = ?", vals)
            conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["validation"] = json.loads(d.pop("validation_json") or "null")
        d["draft"] = json.loads(d.pop("draft_json") or "null")
        d["had_draft"] = bool(d["had_draft"])
        return d

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["validation"] = json.loads(d.pop("validation_json") or "null")
            d["draft"] = json.loads(d.pop("draft_json") or "null")
            d["had_draft"] = bool(d["had_draft"])
            out.append(d)
        return out

    def recent_run_summaries(self, limit: int = 100) -> list[dict[str, Any]]:
        runs = self.list_runs(limit=limit)
        return [
            {
                "guardrail_verdict": r.get("guardrail_verdict"),
                "reason_code": r.get("reason_code") or "",
                "had_draft": r.get("had_draft"),
                "ungrounded_rate": r.get("ungrounded_rate") or 0,
            }
            for r in runs
        ]

    def append_review(
        self,
        *,
        run_id: str,
        gate: str,
        decision: str,
        actor: str,
        reason: str,
        edited_draft: str | None = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO review_events(
                  run_id, gate, decision, actor, reason, edited_draft, created_at
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    gate,
                    decision,
                    actor,
                    reason,
                    edited_draft,
                    datetime.now(UTC).isoformat(),
                ),
            )
            conn.commit()

    def list_review_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM review_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def append_exception(self, reason_code: str, detail: str, run_id: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO exceptions(reason_code, detail, run_id, created_at) VALUES (?,?,?,?)",
                (reason_code, detail, run_id, datetime.now(UTC).isoformat()),
            )
            conn.commit()

    def list_exceptions(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM exceptions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def log_send(self, run_id: str, detail: str = "stub send — nothing transmitted") -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO send_log(run_id, event, detail, created_at) VALUES (?,?,?,?)",
                (run_id, "send_stub", detail, datetime.now(UTC).isoformat()),
            )
            conn.commit()

    def new_trace_id(self) -> str:
        return f"tr-{uuid.uuid4().hex[:12]}"
