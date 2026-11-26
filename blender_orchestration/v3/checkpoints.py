"""Hashed checkpoints with separate best-technical and best-reviewed pointers.

Checkpoint files are never overwritten: every save gets a unique path, and "best" is a pointer
in ``checkpoints/registry.json``. A failed or regressing correction therefore cannot destroy a
verified candidate. Every pointer is re-hashed before use; a missing or altered file is invalid.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

BLEND_MAGICS = (b"BLENDER", b"\x28\xb5\x2f\xfd", b"\x1f\x8b")


class CheckpointError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_atomic(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=False, default=str)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def is_blend_file(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size <= 1024:
            return False
        with open(path, "rb") as fh:
            head = fh.read(7)
        return any(head.startswith(m) for m in BLEND_MAGICS)
    except OSError:
        return False


@dataclass
class CheckpointEntry:
    checkpoint_id: str
    path: str
    sha256: str
    bytes: int
    stage: str
    label: str
    kind: str  # technical | reviewed | diagnostic
    score: float | None = None
    critical_defects: int | None = None
    review_path: str | None = None
    created_utc: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CheckpointManager:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)
        self.dir = self.run_dir / "checkpoints"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.dir / "registry.json"
        self.entries: dict[str, CheckpointEntry] = {}
        self.best_technical_id: str | None = None
        self.best_reviewed: dict[str, str] = {}
        self._counter = 0
        if self.registry_path.is_file():
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            for raw in data.get("entries", []):
                entry = CheckpointEntry(**raw)
                self.entries[entry.checkpoint_id] = entry
            self.best_technical_id = data.get("best_technical_id")
            self.best_reviewed = dict(data.get("best_reviewed") or {})
            self._counter = int(data.get("counter", len(self.entries)))

    # -- paths -----------------------------------------------------------
    def new_path(self, stage: str, label: str) -> Path:
        self._counter += 1
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in f"{stage}_{label}")
        path = self.dir / f"{self._counter:03d}_{safe}.blend"
        while path.exists():
            self._counter += 1
            path = self.dir / f"{self._counter:03d}_{safe}.blend"
        return path

    def _save(self) -> None:
        write_json_atomic(self.registry_path, {
            "schema": "blender_mcp.checkpoint_registry.v3",
            "counter": self._counter,
            "best_technical_id": self.best_technical_id,
            "best_reviewed": self.best_reviewed,
            "entries": [e.to_dict() for e in self.entries.values()],
        })

    # -- registration ----------------------------------------------------
    def register(self, path: Path, *, stage: str, label: str, kind: str = "technical",
                 extra: dict[str, Any] | None = None) -> CheckpointEntry:
        path = Path(path).resolve()
        if not is_blend_file(path):
            raise CheckpointError(f"not a valid .blend checkpoint: {path}")
        if any(e.path == str(path) for e in self.entries.values()):
            raise CheckpointError(f"checkpoint path already registered (never overwrite): {path}")
        entry = CheckpointEntry(
            checkpoint_id=f"ckpt_{len(self.entries) + 1:03d}", path=str(path), sha256=sha256_file(path),
            bytes=path.stat().st_size, stage=stage, label=label, kind=kind,
            created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), extra=dict(extra or {}),
        )
        self.entries[entry.checkpoint_id] = entry
        if kind in ("technical", "reviewed"):
            self.best_technical_id = entry.checkpoint_id
        self._save()
        return entry

    def verify(self, checkpoint_id: str | None) -> CheckpointEntry:
        if not checkpoint_id or checkpoint_id not in self.entries:
            raise CheckpointError(f"unknown checkpoint {checkpoint_id!r}")
        entry = self.entries[checkpoint_id]
        path = Path(entry.path)
        if not path.is_file():
            raise CheckpointError(f"checkpoint file missing: {path}")
        if sha256_file(path) != entry.sha256:
            raise CheckpointError(f"checkpoint hash mismatch: {path}")
        return entry

    # -- reviewed promotion ---------------------------------------------
    def attach_review(self, checkpoint_id: str, *, score: float, critical_defects: int, review_path: str | None) -> None:
        entry = self.verify(checkpoint_id)
        entry.kind = "reviewed"
        entry.score = float(score)
        entry.critical_defects = int(critical_defects)
        entry.review_path = review_path
        self._save()

    def best_reviewed_entry(self, scope: str) -> CheckpointEntry | None:
        cid = self.best_reviewed.get(scope)
        return self.verify(cid) if cid else None

    def consider_promotion(self, scope: str, checkpoint_id: str, *, min_delta: float = 0.5) -> dict[str, Any]:
        """Promote ``checkpoint_id`` to best-reviewed for ``scope`` only if it is not a regression.

        Better = fewer critical defects, or the same number with a score at least ``min_delta`` higher.
        """
        cand = self.verify(checkpoint_id)
        if cand.score is None or cand.critical_defects is None:
            raise CheckpointError("candidate has no attached review")
        cur_id = self.best_reviewed.get(scope)
        if cur_id == checkpoint_id:
            return {"promoted": True, "reason": "already selected", "delta": 0}
        if cur_id is None:
            self.best_reviewed[scope] = checkpoint_id
            self._save()
            return {"promoted": True, "reason": "first reviewed candidate", "delta": None}
        cur = self.verify(cur_id)
        delta = round(cand.score - float(cur.score or 0.0), 2)
        if cand.critical_defects < (cur.critical_defects or 0) and delta >= -1.0:
            better, reason = True, "fewer critical defects"
        elif cand.critical_defects == (cur.critical_defects or 0) and delta >= min_delta:
            better, reason = True, f"score improved by {delta}"
        else:
            better, reason = False, f"regression or no improvement (delta {delta}, critical {cand.critical_defects} vs {cur.critical_defects})"
        if better:
            self.best_reviewed[scope] = checkpoint_id
            self._save()
        return {"promoted": better, "reason": reason, "delta": delta, "previous_best": cur_id}

    def summary(self) -> dict[str, Any]:
        return {
            "best_technical_id": self.best_technical_id,
            "best_reviewed": dict(self.best_reviewed),
            "count": len(self.entries),
        }


def validate_state_against_registry(state: dict[str, Any], manager: CheckpointManager) -> list[str]:
    """Every checkpoint referenced by production state must exist with a matching hash."""
    problems: list[str] = []
    refs: list[tuple[str, str]] = []
    for field in ("resume_checkpoint_id", "approved_checkpoint_id"):
        if state.get(field):
            refs.append((field, state[field]))
    pending = state.get('pending_review')
    if pending and pending.get('checkpoint') != state.get('resume_checkpoint_id'):
        problems.append('Pending review and resume point identify different checkpoints')
    for scope, cid in (state.get("best_reviewed") or {}).items():
        refs.append((f"best_reviewed[{scope}]", cid))
    for label, cid in refs:
        try:
            entry = manager.verify(cid)
        except CheckpointError as exc:
            problems.append(f"{label}: {exc}")
            continue
        recorded = (state.get("checkpoint_hashes") or {}).get(cid)
        if recorded != entry.sha256:
            problems.append(f"{label}: state hash is missing or differs from registry")
    return problems
