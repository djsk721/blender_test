"""Explicit stage status ledger.

A stage is PASS only when it was executed *and* its gate accepted evidence. Booleans that
default to True (the legacy ``rig_validated``) are not representable here.
"""

from __future__ import annotations

import datetime
import enum
from dataclasses import dataclass, field
from typing import Any


class StageStatus(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_VERIFIED = "NOT_VERIFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    BLOCKED = "BLOCKED"


TERMINAL = {StageStatus.PASS, StageStatus.FAIL, StageStatus.NOT_VERIFIED, StageStatus.NOT_APPLICABLE, StageStatus.BLOCKED}


class StageLedgerError(RuntimeError):
    pass


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@dataclass
class StageRecord:
    name: str
    status: StageStatus = StageStatus.NOT_STARTED
    executions: int = 0
    evidence: list[str] = field(default_factory=list)
    score: float | None = None
    reason: str = ""
    started_utc: str | None = None
    finished_utc: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "status": self.status.value, "executions": self.executions,
            "evidence": list(self.evidence), "score": self.score, "reason": self.reason,
            "started_utc": self.started_utc, "finished_utc": self.finished_utc,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StageRecord":
        return cls(
            name=str(data["name"]), status=StageStatus(data.get("status", "NOT_STARTED")),
            executions=int(data.get("executions", 0)), evidence=list(data.get("evidence") or []),
            score=data.get("score"), reason=str(data.get("reason") or ""),
            started_utc=data.get("started_utc"), finished_utc=data.get("finished_utc"),
        )


class StageLedger:
    """Ordered stage records with transition rules.

    Rules enforced:
    - PASS requires at least one recorded execution and at least one evidence item.
    - NOT_APPLICABLE requires a reason and zero executions (it was legitimately skipped).
    - A stage cannot start unless every prerequisite is PASS or NOT_APPLICABLE.
    """

    def __init__(self, stages: list[str], prerequisites: dict[str, list[str]] | None = None) -> None:
        self.order = list(stages)
        self.records = {name: StageRecord(name) for name in stages}
        self.prerequisites = {k: list(v) for k, v in (prerequisites or {}).items()}

    def get(self, name: str) -> StageRecord:
        if name not in self.records:
            raise StageLedgerError(f"unknown stage {name!r}")
        return self.records[name]

    def status(self, name: str) -> StageStatus:
        return self.get(name).status

    def unmet_prerequisites(self, name: str) -> list[str]:
        ok = {StageStatus.PASS, StageStatus.NOT_APPLICABLE}
        return [p for p in self.prerequisites.get(name, []) if self.get(p).status not in ok]

    def start(self, name: str) -> StageRecord:
        unmet = self.unmet_prerequisites(name)
        if unmet:
            raise StageLedgerError(f"stage {name!r} blocked by unmet prerequisites: {unmet}")
        rec = self.get(name)
        rec.status = StageStatus.RUNNING
        rec.started_utc = rec.started_utc or _now()
        return rec

    def record_execution(self, name: str, evidence: str | None = None) -> None:
        rec = self.get(name)
        if rec.status != StageStatus.RUNNING:
            raise StageLedgerError(f"stage {name!r} is not RUNNING")
        rec.executions += 1
        if evidence:
            rec.evidence.append(evidence)

    def finish(self, name: str, status: StageStatus, *, reason: str = "", score: float | None = None,
               evidence: list[str] | None = None) -> StageRecord:
        rec = self.get(name)
        if status not in TERMINAL:
            raise StageLedgerError(f"{status} is not a terminal status")
        rec.evidence.extend(evidence or [])
        if status == StageStatus.PASS:
            if rec.status != StageStatus.RUNNING:
                raise StageLedgerError(f"stage {name!r} cannot PASS without having run")
            if rec.executions < 1 or not rec.evidence:
                raise StageLedgerError(f"stage {name!r} cannot PASS without executions and evidence")
        if status == StageStatus.NOT_APPLICABLE:
            if not reason:
                raise StageLedgerError("NOT_APPLICABLE requires a reason")
            if rec.executions:
                raise StageLedgerError(f"stage {name!r} executed and cannot be NOT_APPLICABLE")
        rec.status = status
        rec.reason = reason
        rec.score = score
        rec.finished_utc = _now()
        return rec

    def block_remaining(self, after: str, reason: str) -> None:
        """Mark every later NOT_STARTED stage as BLOCKED (never PASS) after a gate failure."""
        idx = self.order.index(after)
        for name in self.order[idx + 1:]:
            rec = self.records[name]
            if rec.status == StageStatus.NOT_STARTED:
                rec.status = StageStatus.BLOCKED
                rec.reason = reason

    def to_dict(self) -> dict[str, Any]:
        return {"order": self.order, "prerequisites": self.prerequisites,
                "stages": {n: self.records[n].to_dict() for n in self.order}}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StageLedger":
        ledger = cls(list(data["order"]), data.get("prerequisites") or {})
        for name, rec in (data.get("stages") or {}).items():
            ledger.records[name] = StageRecord.from_dict(rec)
        return ledger

    def validate_consistency(self) -> list[str]:
        """Return problems that would make the recorded state untrustworthy."""
        problems: list[str] = []
        for name in self.order:
            rec = self.records[name]
            if rec.status == StageStatus.PASS and (rec.executions < 1 or not rec.evidence):
                problems.append(f"{name}: PASS without execution evidence")
            if rec.status == StageStatus.PASS and self.unmet_prerequisites(name):
                problems.append(f"{name}: PASS while prerequisites unmet {self.unmet_prerequisites(name)}")
            if rec.status == StageStatus.NOT_APPLICABLE and rec.executions:
                problems.append(f"{name}: NOT_APPLICABLE but executed")
        return problems
