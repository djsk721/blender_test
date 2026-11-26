"""Multi-reference manifest for reference-guided Blender production.

The supervised harness originally accepted exactly one approved reference image
(``--reference-image``), which is too narrow for a character production: a
likeness needs a full-body silhouette, a face close-up, a costume sheet and a
turnaround at the same time. This module keeps the single-primary evidence chain
the harness already records (``approved_reference_path`` + ``approved_reference_sha256``)
and attaches the remaining approved references alongside it, so every reviewer
sees the whole set while the provenance of the primary stays intact.

Schema: ``blender_mcp.reference_manifest.v1``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA = "blender_mcp.reference_manifest.v1"

ROLE_PRIMARY = "primary"
ROLE_SUPPLEMENTARY = "supplementary"
# Accepted aliases, so callers may spell the roles either way.
PRIMARY_ROLE = ROLE_PRIMARY
SUPPLEMENTARY_ROLE = ROLE_SUPPLEMENTARY
VALID_ROLES = (ROLE_PRIMARY, ROLE_SUPPLEMENTARY)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


class ReferenceManifestError(ValueError):
    """Raised when a reference manifest is missing, duplicated or tampered.

    Subclasses ``ValueError`` because the harness treats a bad reference
    configuration as a value error and its tests assert that.
    """


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _image_dimensions(path: Path) -> Tuple[int, int]:
    """Return (width, height) using Pillow when available, else (0, 0)."""
    try:
        from PIL import Image  # type: ignore
    except Exception:
        return (0, 0)
    try:
        with Image.open(path) as image:
            return (int(image.width), int(image.height))
    except Exception:
        return (0, 0)


@dataclass
class ReferenceEntry:
    """One approved reference image with its role and verified hash.

    Mutable on purpose: callers (and tests) reassign ``role`` to promote or
    demote an entry before validating.
    """

    path: Path
    role: str
    sha256: str
    width: int = 0
    height: int = 0
    purpose: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "path": str(self.path),
            "role": self.role,
            "sha256": self.sha256,
            "width": self.width,
            "height": self.height,
        }
        if self.purpose:
            data["purpose"] = self.purpose
        return data


@dataclass
class ReferenceManifest:
    """A validated set of approved references with exactly one primary."""

    schema: str = SCHEMA
    asset: str = ""
    entries: List[ReferenceEntry] = field(default_factory=list)
    path: Optional[Path] = None

    # -- role access -----------------------------------------------------
    @property
    def primary(self) -> Optional[ReferenceEntry]:
        for entry in self.entries:
            if entry.role == ROLE_PRIMARY:
                return entry
        return None

    @property
    def supplementary(self) -> List[ReferenceEntry]:
        return [entry for entry in self.entries if entry.role == ROLE_SUPPLEMENTARY]

    def reference_count(self) -> int:
        return len(self.entries)

    def all_paths(self) -> List[Path]:
        """Every reference path, primary first, then supplementary in order."""
        primary = self.primary
        ordered: List[Path] = []
        if primary is not None:
            ordered.append(Path(primary.path))
        ordered.extend(Path(entry.path) for entry in self.supplementary)
        return ordered

    def primary_path(self) -> Optional[Path]:
        primary = self.primary
        return Path(primary.path) if primary is not None else None

    # -- validation ------------------------------------------------------
    def validate(self) -> "ReferenceManifest":
        return validate_manifest(self)

    # -- serialisation ---------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "asset": self.asset,
            "reference_count": self.reference_count(),
            "primary": str(self.primary.path) if self.primary is not None else None,
            "references": [entry.to_dict() for entry in self.entries],
        }

    # -- prompt rendering ------------------------------------------------
    def prompt_block(self) -> str:
        """Render the reference set for inclusion in a reviewer prompt."""
        lines: List[str] = []
        primary = self.primary
        if primary is not None:
            lines.append(
                f"[PRIMARY reference] {Path(primary.path).name} "
                f"(sha256 {primary.sha256[:12]}) - the authoritative likeness target."
            )
        supplementary = self.supplementary
        if supplementary:
            listed = ", ".join(
                f"{Path(entry.path).name} (sha256 {entry.sha256[:12]})"
                for entry in supplementary
            )
            lines.append(
                f"[SUPPLEMENTARY references] {listed} - additional approved views; "
                "judge the model against the FULL reference set, not one image alone."
            )
        return "\n".join(lines)


def validate_manifest(manifest: ReferenceManifest) -> ReferenceManifest:
    """Reject a manifest that is not exactly-one-primary, duplicate-free, role-valid."""
    if not isinstance(manifest, ReferenceManifest):
        raise ReferenceManifestError("reference manifest must be a ReferenceManifest")
    # Roles are validated first: an unknown role on the primary entry would otherwise
    # be reported as "no primary", which sends the caller hunting the wrong problem.
    for entry in manifest.entries:
        if entry.role not in VALID_ROLES:
            raise ReferenceManifestError(f"unknown reference role: {entry.role!r}")
    primaries = [entry for entry in manifest.entries if entry.role == ROLE_PRIMARY]
    if len(primaries) != 1:
        raise ReferenceManifestError(
            f"a reference manifest requires exactly one primary reference, found {len(primaries)}"
        )
    # The primary is also the FIRST entry: every consumer that predates multi-reference
    # (the harness evidence chain, single-reference callers) reads entries[0] as the
    # approved reference, so a primary parked anywhere else would silently point the
    # evidence chain at a supplementary image.
    if manifest.entries[0].role != ROLE_PRIMARY:
        raise ReferenceManifestError(
            "a reference manifest requires exactly one primary reference and it must "
            "be its first entry"
        )
    seen: set = set()
    for entry in manifest.entries:
        key = str(Path(entry.path).resolve())
        if key in seen:
            raise ReferenceManifestError(f"duplicate reference path: {entry.path}")
        seen.add(key)
    return manifest


def discover_reference_images(directory: Path) -> List[Path]:
    """Return every image file under ``directory`` (recursive), sorted by path."""
    directory = Path(directory)
    if not directory.is_dir():
        raise ReferenceManifestError(f"reference directory is not a directory: {directory}")
    found = sorted(
        {
            path.resolve()
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        },
        key=lambda p: str(p),
    )
    if not found:
        raise ReferenceManifestError(f"no reference images found in {directory}")
    return found


def build_entry(path: Path, role: str) -> ReferenceEntry:
    path = Path(path)
    width, height = _image_dimensions(path)
    return ReferenceEntry(
        path=path.resolve(),
        role=role,
        sha256=_sha256(path),
        width=width,
        height=height,
    )


def build_manifest_from_directory(
    directory: Path,
    asset: str = "",
    primary: Optional[Path] = None,
) -> ReferenceManifest:
    """Build a manifest from every image in ``directory``.

    The first image in sorted order is the primary unless ``primary`` names one
    explicitly (which must be inside the discovered set).
    """
    images = discover_reference_images(directory)
    if primary is not None:
        primary_resolved = Path(primary).resolve()
        if primary_resolved not in images:
            raise ReferenceManifestError(
                f"declared primary reference is not among the discovered images: {primary}"
            )
        primary_path = primary_resolved
    else:
        primary_path = images[0]
    ordered = [primary_path, *(path for path in images if path != primary_path)]
    entries = [
        build_entry(path, ROLE_PRIMARY if path == primary_path else ROLE_SUPPLEMENTARY)
        for path in ordered
    ]
    manifest = ReferenceManifest(asset=asset, entries=entries, path=None)
    validate_manifest(manifest)
    # Persist a sidecar so the manifest has a loadable path for the harness.
    manifest.path = write_reference_manifest(manifest, Path(directory) / "reference_manifest.json")
    return manifest


def write_reference_manifest(manifest: ReferenceManifest, path: Path) -> Path:
    """Write the manifest as JSON; relative paths resolve against its directory."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(manifest.to_dict(), handle, indent=2, sort_keys=True)
    return path


def load_reference_manifest(path: Path) -> ReferenceManifest:
    """Load and validate a manifest, resolving relative paths and verifying hashes."""
    path = Path(path)
    if not path.is_file():
        raise ReferenceManifestError(f"reference manifest does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReferenceManifestError(f"reference manifest is not valid JSON: {path}: {exc}") from exc
    if payload.get("schema") != SCHEMA:
        raise ReferenceManifestError(
            f"reference manifest schema must be {SCHEMA}, got {payload.get('schema')!r}"
        )
    base = path.parent
    entries: List[ReferenceEntry] = []
    for record in payload.get("references", []):
        raw = Path(record["path"])
        resolved = raw if raw.is_absolute() else (base / raw)
        recorded = record.get("sha256")
        if not resolved.is_file():
            # A missing file cannot be hashed, so the recorded hash is unverifiable
            # rather than proven correct: name both facts, because either one alone
            # sends a caller to fix the wrong thing (the path, or the manifest).
            raise ReferenceManifestError(
                f"reference image does not exist: {resolved} - the recorded hash "
                f"cannot be verified, so a hash mismatch cannot be excluded"
            )
        actual = _sha256(resolved)
        if recorded != actual:
            raise ReferenceManifestError(
                f"reference hash mismatch for {resolved}: manifest {recorded}, actual {actual}"
            )
        width, height = _image_dimensions(resolved)
        entries.append(
            ReferenceEntry(
                path=resolved,
                role=record.get("role", ROLE_SUPPLEMENTARY),
                sha256=actual,
                width=width,
                height=height,
                purpose=str(record.get("purpose") or ""),
            )
        )
    manifest = ReferenceManifest(
        schema=payload.get("schema", SCHEMA),
        asset=payload.get("asset", ""),
        entries=entries,
        path=path,
    )
    return validate_manifest(manifest)


def snapshot_references(
    manifest: ReferenceManifest,
    dest_dir: Path,
    manifest_name: str = "reference_manifest.json",
) -> Tuple[List[Path], ReferenceManifest]:
    """Copy every approved reference into ``dest_dir`` and verify hashes.

    Returns ``(snapshot_paths, snapshot_manifest)``: the copied files (primary
    first) and a manifest whose entries point at the copies. A source whose hash
    no longer matches the manifest is refused before anything is written, so a
    tampered reference never reaches the run directory.
    """
    validate_manifest(manifest)
    dest_dir = Path(dest_dir)
    sources = manifest.all_paths()
    if len({p.name for p in sources}) != len(sources):
        raise ReferenceManifestError('snapshot requires unique reference filenames')
    # Verify every source hash before copying a single byte.
    for entry in manifest.entries:
        source = Path(entry.path)
        if not source.is_file():
            raise ReferenceManifestError(f"reference image does not exist: {source}")
        actual = _sha256(source)
        if actual != entry.sha256:
            raise ReferenceManifestError(
                f"reference hash mismatch for {source}: manifest {entry.sha256}, actual {actual}"
            )
    dest_dir.mkdir(parents=True, exist_ok=True)
    snapshot_entries: List[ReferenceEntry] = []
    snapshot_paths: List[Path] = []
    for entry in manifest.entries:
        source = Path(entry.path)
        target = dest_dir / source.name
        target.write_bytes(source.read_bytes())
        copied_hash = _sha256(target)
        if copied_hash != entry.sha256:
            raise ReferenceManifestError(
                f"snapshot hash mismatch for {target}: expected {entry.sha256}, got {copied_hash}"
            )
        snapshot_entries.append(
            ReferenceEntry(
                path=target,
                role=entry.role,
                sha256=copied_hash,
                width=entry.width,
                height=entry.height,
                purpose=entry.purpose,
            )
        )
    # Preserve primary-first ordering in the returned paths.
    for entry in snapshot_entries:
        if entry.role == ROLE_PRIMARY:
            snapshot_paths.append(Path(entry.path))
    for entry in snapshot_entries:
        if entry.role != ROLE_PRIMARY:
            snapshot_paths.append(Path(entry.path))
    snapshot_manifest = ReferenceManifest(
        schema=manifest.schema,
        asset=manifest.asset,
        entries=snapshot_entries,
        path=None,
    )
    write_reference_manifest(snapshot_manifest, dest_dir / manifest_name)
    snapshot_manifest.path = dest_dir / manifest_name
    return snapshot_paths, snapshot_manifest


# ---------------------------------------------------------------------------
# V3 additions: integrity, purposes, overlap detection, bounded review copies
# ---------------------------------------------------------------------------

def verify_image_integrity(path: Path) -> Tuple[int, int]:
    """Fully decode ``path``; raise ReferenceManifestError when it is truncated or not an image."""
    try:
        from PIL import Image  # type: ignore
    except Exception as exc:  # pragma: no cover - Pillow is a hard dependency of the harness
        raise ReferenceManifestError("Pillow is required to validate reference images") from exc
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image.load()
            return (int(image.width), int(image.height))
    except Exception as exc:
        raise ReferenceManifestError(f"reference image is corrupt or unreadable: {path}: {exc}") from exc


def _thumb(path: Path, box: Optional[Tuple[float, float, float, float]] = None, size: int = 48):
    from PIL import Image  # type: ignore

    with Image.open(path) as image:
        image = image.convert("L")
        if box:
            w, h = image.size
            image = image.crop((int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)))
        image = image.resize((size, size))
        return list(image.getdata())


def _correlation(a: List[int], b: List[int]) -> float:
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = sum((x - ma) ** 2 for x in a) ** 0.5
    db = sum((y - mb) ** 2 for y in b) ** 0.5
    return num / (da * db) if da and db else 0.0


_CROP_BOXES = {
    "full": (0.0, 0.0, 1.0, 1.0), "left_half": (0.0, 0.0, 0.5, 1.0), "right_half": (0.5, 0.0, 1.0, 1.0),
    "top_half": (0.0, 0.0, 1.0, 0.5), "bottom_half": (0.0, 0.5, 1.0, 1.0),
}


def detect_reference_overlaps(entries: Sequence[ReferenceEntry], threshold: float = 0.88) -> List[Dict[str, Any]]:
    """Report references whose content is (a crop of) another reference, so duplicated
    information is documented instead of being weighted twice."""
    found: List[Dict[str, Any]] = []
    for small in entries:
        small_thumb = _thumb(Path(small.path))
        for large in entries:
            if large is small or (large.width * large.height) < (small.width * small.height):
                continue
            for region, box in _CROP_BOXES.items():
                corr = _correlation(small_thumb, _thumb(Path(large.path), box))
                if corr >= threshold:
                    found.append({"image": Path(small.path).name, "contained_in": Path(large.path).name,
                                  "region": region, "correlation": round(corr, 3)})
                    break
    return found


def build_manifest_with_primary(
    directory: Path,
    primary_name: str,
    *,
    asset: str = "",
    purposes: Optional[Dict[str, str]] = None,
    out_path: Optional[Path] = None,
) -> ReferenceManifest:
    """Discover every image, verify that each decodes, assign the named primary, and record
    purposes. The manifest is written to ``out_path`` (never into the reference directory)."""
    images = discover_reference_images(directory)
    by_name = {p.name: p for p in images}
    if primary_name not in by_name:
        raise ReferenceManifestError(f"primary reference {primary_name!r} not found among {sorted(by_name)}")
    ordered = [by_name[primary_name]] + [p for p in images if p.name != primary_name]
    entries: List[ReferenceEntry] = []
    for path in ordered:
        width, height = verify_image_integrity(path)
        entries.append(ReferenceEntry(
            path=path.resolve(), role=ROLE_PRIMARY if path.name == primary_name else ROLE_SUPPLEMENTARY,
            sha256=_sha256(path), width=width, height=height, purpose=(purposes or {}).get(path.name, ""),
        ))
    manifest = validate_manifest(ReferenceManifest(asset=asset, entries=entries))
    if out_path is not None:
        manifest.path = write_reference_manifest(manifest, Path(out_path))
    return manifest


def make_review_copies(manifest: ReferenceManifest, dest_dir: Path, max_side: int = 1280) -> List[Dict[str, Any]]:
    """Bounded JPEG copies for model attachments (keeps image payloads small). Each copy records
    the SHA-256 of its source so provenance stays traceable to the approved original."""
    from PIL import Image  # type: ignore

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out: List[Dict[str, Any]] = []
    for idx, entry in enumerate(manifest.entries):
        src = Path(entry.path)
        if _sha256(src) != entry.sha256:
            raise ReferenceManifestError(f"reference changed since manifest creation: {src}")
        target = dest_dir / f"ref{idx + 1}_{entry.role}_{src.name.split('.')[0]}.jpg"
        with Image.open(src) as image:
            image = image.convert("RGB")
            image.thumbnail((max_side, max_side))
            image.save(target, "JPEG", quality=90)
        out.append({"source": str(src), "source_sha256": entry.sha256, "role": entry.role,
                    "purpose": entry.purpose, "review_copy": str(target), "copy_sha256": _sha256(target),
                    "copy_size": list(Image.open(target).size)})
    return out
