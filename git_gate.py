"""Read-only checks of the proposed Git index, never of worktree contents.

Git stores files, not empty directories. Directory evidence is therefore
inferred from indexed paths; an empty directory recorded by a filesystem
snapshot requires review instead of being silently treated as present.
Snapshots attest byte evidence only. A clean gate does not approve semantics.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import living_docs as living

_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_REGULAR_MODES = frozenset({"100644", "100755"})
_FACT_BYTES = 1024 * 1024
DIRECTORY_MODEL = "inferred_from_indexed_paths_no_empty_directories"


@dataclass(frozen=True)
class _Entry:
    path: str
    mode: str
    oid: str


def _index_path(value: str) -> str:
    """Validate Git paths without applying the snapshot's coverage exclusions."""
    path = PurePosixPath(value)
    if (
        not value
        or "\x00" in value
        or "\\" in value
        or path.is_absolute()
        or PureWindowsPath(value).drive
        or ".." in path.parts
        or value == "."
        or path.as_posix() != value
    ):
        raise ValueError(f"Ruta no portable o no canónica en Git: {value!r}")
    return value


def _decode(data: bytes, label: str) -> str:
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError(f"{label} debe ser UTF-8 válido.") from exc


class _Git:
    def __init__(self, root: Path):
        self.root = root
        executable = shutil.which("git")
        if executable is None:
            raise ValueError("Git no está disponible.")
        self.executable = Path(executable).resolve(strict=True)
        if self.executable.is_relative_to(root):
            raise ValueError("El ejecutable Git no puede provenir del repositorio inspeccionado.")
        self.environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.environment.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_OPTIONAL_LOCKS="0",
            GIT_TERMINAL_PROMPT="0",
            GIT_NO_REPLACE_OBJECTS="1",
            GIT_NO_LAZY_FETCH="1",
            LC_ALL="C",
        )
        # Git supplies an alternate index to hooks for partial commits. Ignoring
        # it could approve a different tree from the one actually being committed.
        self.index_name = "default"
        if "GIT_INDEX_FILE" in os.environ:
            value = os.environ["GIT_INDEX_FILE"]
            if not value or "\x00" in value:
                raise ValueError("GIT_INDEX_FILE debe identificar un archivo regular existente.")
            index = living._safe_path(Path(value))
            if living._kind(index, missing_ok=True) != "file":
                raise ValueError("GIT_INDEX_FILE debe identificar un archivo regular existente.")
            self.environment["GIT_INDEX_FILE"] = str(index)
            self.index_name = "GIT_INDEX_FILE"
        top = _decode(self.run("rev-parse", "--show-toplevel"), "La raíz de Git").removesuffix("\n")
        if Path(top) != root:
            raise ValueError("ROOT debe ser la raíz del worktree Git, no un subdirectorio.")

    def run(self, *arguments: str, allow_missing_head: bool = False) -> bytes:
        try:
            result = subprocess.run(
                [
                    str(self.executable),
                    "--no-pager",
                    "--no-optional-locks",
                    "--literal-pathspecs",
                    "-c",
                    "core.fsmonitor=false",
                    "-c",
                    f"core.hooksPath={os.devnull}",
                    "-c",
                    "protocol.allow=never",
                    "-C",
                    str(self.root),
                    *arguments,
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=30.0,
                env=self.environment,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"No se pudo completar la lectura de Git: {exc}") from exc
        if allow_missing_head and result.returncode == 1:
            return b""
        if result.returncode:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise ValueError(f"Git no pudo leer {arguments[0]}: {detail[:1000]}")
        return result.stdout

    def head(self) -> str | None:
        value = self.run("rev-parse", "--verify", "--quiet", "HEAD", allow_missing_head=True)
        if not value:
            return None
        oid = _decode(value, "HEAD").strip()
        if not _OBJECT_ID.fullmatch(oid):
            raise ValueError("Git devolvió un HEAD inválido.")
        return oid

    def blob(self, entry: _Entry, *, maximum: int | None = None) -> bytes:
        if maximum is not None:
            size = self.run("cat-file", "-s", entry.oid).strip()
            if not size.isdigit() or int(size) > maximum:
                raise ValueError(f"El archivo excede el tamaño permitido: {entry.path}")
        data = self.run("cat-file", "blob", entry.oid)
        if maximum is not None and len(data) > maximum:
            raise ValueError(f"El archivo excede el tamaño permitido: {entry.path}")
        return data


def _entry(mode: bytes, oid: bytes, path: bytes) -> _Entry:
    relative = _index_path(_decode(path, "La ruta Git"))
    mode_text = _decode(mode, "El modo Git")
    oid_text = _decode(oid, "El objeto Git")
    if mode_text not in _REGULAR_MODES:
        raise ValueError(f"Sólo se admiten archivos regulares en Git; modo {mode_text}: {relative}")
    if not _OBJECT_ID.fullmatch(oid_text):
        raise ValueError(f"Identificador de objeto Git inválido: {relative}")
    return _Entry(relative, mode_text, oid_text)


def _read_index(git: _Git) -> tuple[bytes, dict[str, _Entry]]:
    raw = git.run("ls-files", "--stage", "-z")
    entries: dict[str, _Entry] = {}
    for record in raw.split(b"\x00"):
        if not record:
            continue
        try:
            metadata, path = record.split(b"\t", 1)
            mode, oid, stage = metadata.split(b" ")
        except ValueError as exc:
            raise ValueError("Git devolvió una entrada de índice inválida.") from exc
        if stage != b"0":
            raise ValueError(f"El índice contiene un conflicto sin resolver: {_decode(path, 'La ruta Git')}")
        entry = _entry(mode, oid, path)
        if entry.path in entries:
            raise ValueError(f"Entrada duplicada en el índice: {entry.path}")
        entries[entry.path] = entry
    for path in entries:
        if any(parent.as_posix() in entries for parent in PurePosixPath(path).parents):
            raise ValueError(f"Un archivo del índice no puede ser padre de otra ruta: {path}")

    # ls-files includes intent-to-add entries, but Git does not commit them.
    # Compare only index metadata (never worktree content) with both visibility
    # policies, otherwise an empty file added with -N could produce false clean.
    def staged_metadata(policy: str) -> dict[bytes, bytes]:
        data = git.run(
            "diff",
            "--cached",
            "--raw",
            "--no-abbrev",
            "-z",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            policy,
        )
        if not data:
            return {}
        parts = data.split(b"\x00")
        if parts[-1] or len(parts[:-1]) % 2:
            raise ValueError("Git devolvió metadatos de diferencias inválidos.")
        return {parts[index + 1]: parts[index] for index in range(0, len(parts) - 1, 2)}

    visible = staged_metadata("--ita-visible-in-index")
    invisible = staged_metadata("--ita-invisible-in-index")
    intended = {
        path for path in visible.keys() | invisible.keys() if visible.get(path) != invisible.get(path)
    }
    if intended:
        names = sorted(_decode(path, "La ruta Git") for path in intended if path)
        raise ValueError(
            "El índice contiene archivos intent-to-add sin contenido staged: " + ", ".join(names)
        )
    return raw, entries


def _head_history(git: _Git, head: str | None, history_path: str) -> dict[str, _Entry]:
    if head is None:
        return {}
    raw = git.run("ls-tree", "-r", "-z", "--full-tree", head, "--", history_path)
    entries: dict[str, _Entry] = {}
    for record in raw.split(b"\x00"):
        if not record:
            continue
        try:
            metadata, path = record.split(b"\t", 1)
            mode, kind, oid = metadata.split(b" ")
        except ValueError as exc:
            raise ValueError("Git devolvió una entrada de árbol inválida.") from exc
        if kind != b"blob":
            raise ValueError("El historial de HEAD contiene un objeto que no es un archivo regular.")
        entry = _entry(mode, oid, path)
        if entry.path == history_path:
            raise ValueError("La ruta de historial en HEAD debe ser un directorio.")
        if entry.path.endswith(".jsonl"):
            entries[entry.path] = entry
    return entries


def _directories(entries: dict[str, _Entry]) -> set[str]:
    return {"."} | {parent.as_posix() for path in entries for parent in PurePosixPath(path).parents}


def _included(path: str) -> bool:
    return not any(part in living.EXCLUSIONS for part in PurePosixPath(path).parts)


def _snapshot(
    git: _Git, entries: dict[str, _Entry], snapshot_path: str, *, maximum_bytes: int | None = None
) -> dict[str, Any]:
    entry = entries.get(snapshot_path)
    if entry is None:
        raise ValueError(f"El snapshot debe estar presente en el índice: {snapshot_path}")
    try:
        raw = git.blob(entry) if maximum_bytes is None else git.blob(entry, maximum=maximum_bytes)
        data = _decode(raw, "El snapshot")
        payload = json.loads(
            data, object_pairs_hook=living._unique_object, parse_constant=living._invalid_constant
        )
    except RecursionError as exc:
        raise ValueError("Snapshot JSON demasiado anidado.") from exc
    baseline = living._validate_manifest(payload)
    if any(
        living._within(snapshot_path, scope["path"]) for scope in baseline["source_scopes"]
    ) or snapshot_path in {item["path"] for item in baseline["documents"]}:
        raise ValueError("El snapshot del índice debe quedar fuera de los alcances y documentos observados.")
    if baseline.get("discovery") and any(
        living._within(snapshot_path, root) for root in baseline["discovery"]["roots"]
    ):
        raise ValueError("El snapshot del índice debe quedar fuera de los discovery roots.")
    return baseline


def _byte_evidence(
    git: _Git,
    entries: dict[str, _Entry],
    baseline: dict[str, Any],
    *,
    maximum_source_bytes: int | None = None,
    maximum_document_bytes: int | None = None,
) -> dict[str, Any]:
    directories = _directories(entries)
    files: dict[str, dict[str, Any]] = {}
    observed_directories: set[str] = set()
    kinds: dict[str, str] = {}

    document_paths = {record["path"] for record in baseline["documents"]}

    def fingerprint(path: str) -> dict[str, Any]:
        if path not in files:
            maximum = maximum_source_bytes
            if path in document_paths and maximum_document_bytes is not None:
                maximum = maximum_document_bytes if maximum is None else min(maximum, maximum_document_bytes)
            data = git.blob(entries[path]) if maximum is None else git.blob(entries[path], maximum=maximum)
            files[path] = {"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        return files[path]

    for scope in baseline["source_scopes"]:
        path = scope["path"]
        kind = "file" if path in entries else "directory" if path in directories else "missing"
        kinds[path] = kind
        if kind == "directory" and scope["kind"] == "directory":
            observed_directories.update(
                directory
                for directory in directories
                if living._within(directory, path) and _included(directory)
            )
            for relative in entries:
                if living._within(relative, path) and _included(relative):
                    fingerprint(relative)
        elif kind == "file":
            fingerprint(path)

    # Capture the source inventory before reading separately declared documents.
    source_files = dict(files)
    documents: dict[str, dict[str, Any]] = {}
    for record in baseline["documents"]:
        path = record["path"]
        if path in entries:
            documents[path] = fingerprint(path)
        elif path in directories:
            raise ValueError(f"El documento del índice debe ser un archivo regular: {path}")
    source_changes = living._diff({item["path"]: item for item in baseline["sources"]}, source_files)
    document_changes = living._diff({item["path"]: item for item in baseline["documents"]}, documents)
    scope_changes = [
        {"path": scope["path"], "before": scope["kind"], "after": kinds[scope["path"]]}
        for scope in sorted(baseline["source_scopes"], key=lambda item: item["path"])
        if kinds[scope["path"]] != scope["kind"]
    ]
    old_directories = set(baseline["source_directories"])
    return {
        "sources": source_changes,
        "documents": document_changes,
        "scope_changes": scope_changes,
        "directory_changes": {
            "added": sorted(observed_directories - old_directories),
            "deleted": sorted(old_directories - observed_directories),
        },
    }


def _history_check(
    git: _Git, entries: dict[str, _Entry], head: str | None, history_path: str
) -> dict[str, Any]:
    from history import MAX_SEGMENT_BYTES, parse_segment

    if history_path in entries:
        raise ValueError("La ruta de historial del índice debe ser un directorio.")
    before = _head_history(git, head, history_path)
    after = {
        path: entry
        for path, entry in entries.items()
        if path.startswith(history_path + "/") and path.endswith(".jsonl")
    }
    report: dict[str, Any] = {
        "state": "append_only",
        "path": history_path,
        "checked_segments": len(after),
        "new_segments": [],
        "appended_segments": [],
        "unchanged_segments": [],
        "violations": [],
    }
    event_ids: dict[str, str] = {}

    def violation(path: str, reason: str, detail: str | None = None) -> None:
        issue = {"path": path, "reason": reason}
        if detail is not None:
            issue["detail"] = detail
        report["violations"].append(issue)

    def register_events(path: str, events: list[dict[str, Any]]) -> None:
        for event in events:
            identifier = event["event_id"]
            previous = event_ids.get(identifier)
            if previous is not None:
                violation(path, "duplicate_event_id", f"event_id {identifier} también aparece en {previous}.")
            else:
                event_ids[identifier] = path

    for path in sorted(before.keys() | after.keys()):
        relative = path.removeprefix(history_path + "/")
        if "/" in relative:
            violation(
                path, "nested_segment", "Los segmentos UUID.jsonl deben estar directamente bajo history."
            )
            continue
        session = PurePosixPath(path).stem
        old_entry, new_entry = before.get(path), after.get(path)
        if new_entry is None:
            violation(path, "deleted", "Un segmento de HEAD no puede borrarse ni renombrarse.")
            continue
        try:
            new = git.blob(new_entry, maximum=MAX_SEGMENT_BYTES)
            if old_entry is None:
                register_events(path, parse_segment(new, expected_session=session))
                report["new_segments"].append(path)
                continue
            old = git.blob(old_entry, maximum=MAX_SEGMENT_BYTES)
            old_events = parse_segment(old, expected_session=session)
        except ValueError as exc:
            violation(path, "invalid_segment", str(exc))
            continue
        if old_events and old_events[-1]["kind"] == "session.closed" and new != old:
            violation(
                path, "closed_segment_changed", "Un segmento cerrado debe conservar exactamente sus bytes."
            )
            continue
        if not new.startswith(old):
            violation(path, "prefix_changed", "Los bytes de HEAD deben conservarse como prefijo exacto.")
            continue
        try:
            register_events(path, parse_segment(new, expected_session=session))
        except ValueError as exc:
            violation(path, "invalid_segment", str(exc))
            continue
        report["unchanged_segments" if new == old else "appended_segments"].append(path)
    if report["violations"]:
        report["state"] = "review_required"
    return report


def _facts_check(git: _Git, entries: dict[str, _Entry], facts_path: str | None) -> dict[str, Any] | None:
    if facts_path is None:
        return None
    import facts

    entry = entries.get(facts_path)
    if entry is None:
        raise ValueError(f"El manifiesto de hechos debe estar presente en el índice: {facts_path}")
    manifest = facts.parse_manifest(_decode(git.blob(entry, maximum=_FACT_BYTES), "El manifiesto de hechos"))

    def read_text(path: str) -> str | None:
        entry = entries.get(path)
        return None if entry is None else _decode(git.blob(entry, maximum=_FACT_BYTES), path)

    return facts.check_manifest(manifest, read_text)


def _knowledge_preservation(git, entries, head, history_path):
    """Verify every HEAD acceptance (including ancestors), then preserve its artifacts.

    History retains its original prefix/closed-segment rules in _history_check.
    This additional check compares only referenced immutable artifacts, not sources.
    """
    if head is None:
        return
    from doc_evidence import from_index
    from doc_revisions import load_acceptances

    old = {}
    for record in git.run("ls-tree", "-r", "-z", "--full-tree", head).split(b"\0"):
        if not record:
            continue
        metadata, name = record.split(b"\t", 1)
        mode, kind, oid = metadata.split(b" ")
        if kind == b"blob" and mode.decode() in _REGULAR_MODES:
            entry = _entry(mode, oid, name)
            old[entry.path] = entry
    reader = from_index(git, b"", old, head)
    load_acceptances(reader, history_path)
    # load_acceptances verifies all manifests, evidence, documents, reviews,
    # maps and original baselines, and records precisely those reads.
    for name in reader._observed:
        if living._within(name, history_path):
            continue
        if entries.get(name) != old.get(name):
            raise ValueError("Accepted artifact changed or missing from index: " + name)


def check_index(
    root: Path,
    snapshot_path: str,
    history_path: str = "docs/living/history",
    facts_path: str | None = None,
    *,
    knowledge_map: str | None = None,
) -> dict[str, Any]:
    """Check staged blobs and append-only history without writing to the repo.

    ROOT must be the Git worktree root. All argument paths are canonical and
    relative to ROOT. Schema/reading errors raise ValueError; valid differences
    and history violations return review_required. GIT_INDEX_FILE is honored
    after validation so Git's alternate index for partial commits is checked.
    """
    directory = living._root(root)
    snapshot_path = living._relative(snapshot_path, canonical=True, allow_root=False)
    history_path = living._relative(history_path, canonical=True, allow_root=False)
    if facts_path is not None:
        facts_path = living._relative(facts_path, canonical=True, allow_root=False)
    git = _Git(directory)
    raw_index, entries = _read_index(git)
    head = git.head()
    if knowledge_map is None:
        baseline = _snapshot(git, entries, snapshot_path)
        evidence = _byte_evidence(git, entries, baseline)
    else:
        from doc_evidence import MAX_EVIDENCE, MAX_SOURCE_BYTES
        from doc_revisions import MAX_DOCUMENT

        baseline = _snapshot(git, entries, snapshot_path, maximum_bytes=MAX_EVIDENCE)
        evidence = _byte_evidence(
            git, entries, baseline, maximum_source_bytes=MAX_SOURCE_BYTES, maximum_document_bytes=MAX_DOCUMENT
        )
    coverage_report = None
    if baseline.get("discovery"):
        from discovery import eligible
        from discovery import report as discovery_report

        policy = baseline["discovery"]
        candidates = {path: "tracked" for path in entries if eligible(path, policy)}
        covered = {item["path"] for item in baseline["sources"] + baseline["documents"]}
        # Directory scopes cover new staged descendants, even without a worktree copy.
        # They still appear as sources.added in byte evidence.
        covered.update(
            path
            for path in candidates
            if any(
                scope["kind"] == "directory" and living._within(path, scope["path"])
                for scope in baseline["source_scopes"]
            )
        )
        coverage_report = discovery_report(policy, "index", candidates, covered)
    history = _history_check(git, entries, head, history_path)
    facts = _facts_check(git, entries, facts_path)
    knowledge = None
    if knowledge_map is not None:
        from doc_evidence import from_index
        from doc_impact import check_knowledge

        _knowledge_preservation(git, entries, head, history_path)
        reader = from_index(git, raw_index, entries, head)
        knowledge = check_knowledge(
            directory, knowledge_map, snapshot_path, view="index", history_path=history_path, read_view=reader
        )
    # Content-addressed reads use the first observed index and pinned HEAD.
    # Detect a changed proposal before returning; this is not an atomic lock.
    if _read_index(git)[0] != raw_index or git.head() != head:
        raise ValueError("El índice o HEAD cambió durante la comprobación; vuelve a ejecutarla.")
    changed = bool(
        (coverage_report and coverage_report["state"] == "uncovered")
        or evidence["scope_changes"]
        or any(evidence["sources"].values())
        or any(evidence["documents"].values())
        or any(evidence["directory_changes"].values())
        or history["state"] != "append_only"
        or (facts and facts["state"] != "in_sync")
        or (knowledge and knowledge["state"] != "no_linked_change")
    )
    return {
        "state": "review_required" if changed else "clean",
        "kit_version": living.VERSION,
        "snapshot_created_at": baseline["created_at"],
        "semantic_review": "not_attested",
        **evidence,
        "history": history,
        "facts": facts,
        **({"knowledge": knowledge} if knowledge is not None else {}),
        **({"coverage_check": coverage_report} if coverage_report is not None else {}),
        "git": {"snapshot_head": baseline["git_head"], "current_head": head, "index": git.index_name},
        "directory_model": DIRECTORY_MODEL,
        "coverage": living.COVERAGE,
        "coverage_note": living.COVERAGE_NOTE + " Se leen exclusivamente blobs del índice; Git no conserva "
        "directorios vacíos. El historial se compara con HEAD y los hechos sólo si se declaran.",
    }
