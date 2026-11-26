"""Model-role policy and producer provenance.

The executor only runs Blender code that (a) was extracted verbatim from a response of the
configured producer model and (b) has a provenance record whose hash matches the code. Text from
the reviewer/supervisor is never executable: it is delivered to the producer as a work order.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REQUIRED_PRODUCER_MODEL_ID = "strata/sc117-qwen38-abliterated-iq3_s"
DEFAULT_REVIEWER_MODEL_ID = "openai/gpt-6.2-sol"

PRODUCER = "producer"
REVIEWER = "reviewer"
HARNESS = "harness"


class RoleViolationError(RuntimeError):
    """Raised when code or a decision would cross the reviewer/producer boundary."""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _base(model_id: str) -> str:
    return str(model_id).partition("#")[0].strip()


@dataclass(frozen=True)
class ModelRoles:
    producer: str
    reviewer: str

    def to_dict(self) -> dict[str, str]:
        return {"producer": self.producer, "reviewer": self.reviewer}


def validate_model_roles(producer: str, reviewer: str, *, required_producer: str = REQUIRED_PRODUCER_MODEL_ID) -> ModelRoles:
    """Enforce: producer is exactly the required Qwen model; reviewer is a different, non-local model."""
    p, r = _base(producer), _base(reviewer)
    if producer != required_producer or p != REQUIRED_PRODUCER_MODEL_ID:
        raise RoleViolationError(
            f"Producer must be {required_producer!r}; got {producer!r}. Silent producer substitution is forbidden."
        )
    if not re.fullmatch(r"[^/\s#]+/[^/\s#]+(?:#[^/\s#]+)?", reviewer):
        raise RoleViolationError("An explicit provider/model ID is required for the reviewer.")
    if r == p or r.startswith('strata/') or 'qwen' in r.casefold():
        raise RoleViolationError("V3 requires a non-Qwen independent vision reviewer.")
    return ModelRoles(producer=producer, reviewer=reviewer)


@dataclass(frozen=True)
class ProvenanceRecord:
    record_id: str
    role: str
    model_id: str
    session_id: str
    work_order_id: str
    prompt_sha256: str
    response_sha256: str
    code_sha256: str
    created_utc: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProvenanceLedger:
    """Append-only provenance log (``provenance.jsonl``) plus an execution authorization check."""

    def __init__(self, roles: ModelRoles, log_path: Path | None = None, *, geometry=None) -> None:
        self.roles = roles
        self.geometry = geometry or {'geometry_strategy': 'FROM_SCRATCH'}
        self.log_path = Path(log_path) if log_path else None
        self._records: dict[str, ProvenanceRecord] = {}
        self._responses: dict[str, str] = {}
        if self.log_path and self.log_path.is_file():
            for line in self.log_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    data = json.loads(line)
                    if data.get("kind") == "provenance":
                        rec = ProvenanceRecord(**data["record"])
                        self._records[rec.record_id] = rec

    def _append(self, payload: dict[str, Any]) -> None:
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, sort_keys=True) + "\n")

    def register_producer_output(self, *, model_id: str, session_id: str, work_order_id: str,
                                 prompt: str, response_text: str, code: str) -> ProvenanceRecord:
        if model_id != REQUIRED_PRODUCER_MODEL_ID or model_id != self.roles.producer:
            raise RoleViolationError(f"Only the producer {self.roles.producer!r} may author Blender code; got {model_id!r}.")
        if not session_id or not code.strip() or code not in response_text:
            raise RoleViolationError("Executable code must be extracted verbatim from the producer response.")
        rec = ProvenanceRecord(
            record_id=f"prov_{len(self._records) + 1:04d}_{sha256_text(code)[:10]}",
            role=PRODUCER, model_id=model_id, session_id=session_id or "", work_order_id=work_order_id,
            prompt_sha256=sha256_text(prompt), response_sha256=sha256_text(response_text),
            code_sha256=sha256_text(code), created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )
        self._records[rec.record_id] = rec
        self._responses[rec.record_id] = response_text
        self._append({"kind": "provenance", "record": rec.to_dict(), "geometry": self.geometry})
        return rec

    def authorize_execution(self, code: str, record: ProvenanceRecord | None) -> ProvenanceRecord:
        """Raise unless ``code`` is exactly the producer-authored code named by ``record``."""
        if record is None:
            raise RoleViolationError("Execution refused: no producer provenance for this code.")
        known = self._records.get(record.record_id)
        if known is None or known != record:
            raise RoleViolationError("Execution refused: unknown or altered provenance record.")
        if record.role != PRODUCER or record.model_id != REQUIRED_PRODUCER_MODEL_ID or record.model_id != self.roles.producer:
            raise RoleViolationError(f"Execution refused: code authored by role={record.role} model={record.model_id}.")
        if sha256_text(code) != record.code_sha256:
            raise RoleViolationError("Execution refused: code differs from the producer-authored code.")
        self._append({"kind": "authorized", "record_id": record.record_id, "code_sha256": record.code_sha256,
                      "geometry": self.geometry})
        return record

    def records(self) -> list[ProvenanceRecord]:
        return list(self._records.values())
