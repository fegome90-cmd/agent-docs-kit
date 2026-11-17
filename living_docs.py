"""Portable byte evidence for explicitly selected sources and documents.

This module never approves documentation or executes inspected project code.
Snapshots are local, unsigned observations, not an atomic view of a live tree.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
import subprocess
from typing import Any

VERSION = "0.5.0-rc.1"
SCHEMA = "agent-docs-kit/living-docs"
SCHEMA_VERSION = 1
EXCLUSIONS = (".git", ".hg", ".svn", ".venv", "node_modules", "__pycache__")
COVERAGE = "declared_sources_and_documents"
COVERAGE_NOTE = (
    "Solo se inspeccionan los alcances de fuentes y documentos declarados, "
    "con las exclusiones registradas. Bytes sin cambios no certifican corrección "
    "semántica, aprobación ni vigencia respecto de información externa."
)
_CHUNK_SIZE = 1024 * 1024
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_HEAD = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_KIT_VERSION = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)


def _safe_path(path: Path) -> Path:
    """Reject symlink components before normalizing any subsequent '..'."""
    absolute = Path(path).expanduser()
    if not absolute.is_absolute():
        absolute = Path.cwd() / absolute
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor = cursor.parent if part == ".." else cursor / part
        try:
            mode = cursor.lstat().st_mode
        except (FileNotFoundError, NotADirectoryError):
            continue
        if stat.S_ISLNK(mode):
            raise ValueError(f"Enlace simbólico no permitido: {cursor}")
    return cursor


def _root(path: Path) -> Path:
    directory = _safe_path(path)
    if _kind(directory, missing_ok=True) != "directory":
        raise ValueError(f"La raíz debe ser un directorio existente: {directory}")
    return directory


def _relative(value: object, *, canonical: bool = False, allow_root: bool = True) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ValueError(f"Ruta relativa inválida: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or PureWindowsPath(value).drive or ".." in path.parts:
        raise ValueError(f"La ruta debe ser relativa y no contener '..': {value!r}")
    normalized = path.as_posix()
    if (normalized == "." and not allow_root) or (canonical and value != normalized):
        raise ValueError(f"Ruta no canónica o no válida para archivo: {value!r}")
    if any(part in EXCLUSIONS for part in path.parts):
        raise ValueError(f"Ruta excluida por la política del snapshot: {value!r}")
    return normalized


def _selected(values: Sequence[str], *, documents: bool = False) -> list[str]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence) or not values:
        raise ValueError("Se requiere al menos una fuente y un documento explícitos.")
    paths = [_relative(value, allow_root=not documents) for value in values]
    if len(paths) != len(set(paths)):
        raise ValueError("No se permiten rutas seleccionadas duplicadas tras normalizar.")
    return sorted(paths)


def _kind(path: Path, *, missing_ok: bool = False) -> str:
    checked = _safe_path(path)
    try:
        mode = checked.lstat().st_mode
    except (FileNotFoundError, NotADirectoryError):
        if missing_ok:
            return "missing"
        raise
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISDIR(mode):
        return "directory"
    raise ValueError(f"Objeto no regular o enlace simbólico no permitido: {checked}")


def _signature(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _fingerprint(path: Path, relative: str, *, chunks: list[bytes] | None = None,
                 maximum_bytes: int | None = None) -> dict[str, Any]:
    """Read only regular files in bounded chunks; propagate incomplete reads."""
    checked = _safe_path(path)
    before = checked.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"Se requiere un archivo regular: {checked}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(checked, flags)
    digest = hashlib.sha256()
    size = 0
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or _signature(opened) != _signature(before):
            raise OSError(f"El archivo cambió antes de leerlo: {checked}")
        while chunk := stream.read(_CHUNK_SIZE):
            if maximum_bytes is not None and size + len(chunk) > maximum_bytes:
                raise ValueError("File size limit exceeded")
            size += len(chunk)
            digest.update(chunk)
            if chunks is not None:
                chunks.append(chunk)
        after = os.fstat(stream.fileno())
    current = _safe_path(checked).lstat()
    if (size != opened.st_size or _signature(opened) != _signature(after)
            or _signature(after) != _signature(current)):
        raise OSError(f"El archivo cambió durante la lectura: {checked}")
    return {"path": relative, "size": size, "sha256": digest.hexdigest()}


def _scan_directory(path: Path, relative: str, files: dict[str, dict[str, Any]],
                    directories: set[str]) -> None:
    if relative in directories:
        return
    checked = _safe_path(path)
    before = checked.lstat()
    if not stat.S_ISDIR(before.st_mode):
        raise OSError(f"El directorio cambió antes de recorrerlo: {checked}")
    directories.add(relative)
    with os.scandir(checked) as iterator:
        entries = sorted(iterator, key=lambda entry: entry.name)
    for entry in entries:
        if entry.name in EXCLUSIONS:
            continue
        child = entry.name if relative == "." else f"{relative}/{entry.name}"
        _relative(child, canonical=True, allow_root=False)
        target = checked / entry.name
        kind = _kind(target)
        if kind == "directory":
            _scan_directory(target, child, files, directories)
        elif child not in files:
            files[child] = _fingerprint(target, child)
    if _signature(_safe_path(checked).lstat()) != _signature(before):
        raise OSError(f"El directorio cambió durante el recorrido: {checked}")


def _collect_sources(root: Path, scopes: list[dict[str, Any]], *, missing_ok: bool = False
                     ) -> tuple[dict[str, dict[str, Any]], set[str], dict[str, str]]:
    files: dict[str, dict[str, Any]] = {}
    directories: set[str] = set()
    kinds: dict[str, str] = {}
    for scope in scopes:
        relative = scope["path"]
        target = root / relative
        kind = _kind(target, missing_ok=missing_ok)
        kinds[relative] = kind
        # A selected file becoming a directory does not expand read coverage.
        # A separately selected parent directory may still cover its children.
        if kind == "directory" and scope["kind"] == "directory":
            _scan_directory(target, relative, files, directories)
        elif kind == "file" and relative not in files:
            files[relative] = _fingerprint(target, relative)
    return files, directories, kinds


def _collect_documents(root: Path, paths: list[str], source_files: dict[str, dict[str, Any]],
                       *, missing_ok: bool = False) -> dict[str, dict[str, Any]]:
    documents: dict[str, dict[str, Any]] = {}
    for relative in paths:
        target = root / relative
        kind = _kind(target, missing_ok=missing_ok)
        if kind == "missing":
            continue
        if kind != "file":
            raise ValueError(f"El documento debe ser un archivo regular: {relative}")
        documents[relative] = (source_files[relative] if relative in source_files
                               else _fingerprint(target, relative))
    return documents


def _identity(path: Path) -> tuple[int, int] | None:
    try:
        info = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return None
    return info.st_dev, info.st_ino


def _outside_observed(output: Path, root: Path, scopes: list[dict[str, Any]], documents: list[str]) -> None:
    """Recheck both lexical containment and existing filesystem aliases."""
    output_id = _identity(output)
    ancestor_ids = {_identity(path) for path in (output, *output.parents)} - {None}
    for scope in scopes:
        target = _safe_path(root / scope["path"])
        if output.is_relative_to(target) or _identity(target) in ancestor_ids:
            raise ValueError("El snapshot debe quedar fuera de todos los alcances de fuentes.")
    for relative in documents:
        target = _safe_path(root / relative)
        if output == target or (output_id is not None and _identity(target) == output_id):
            raise ValueError("El snapshot no puede ser un documento observado.")


def _git_head(root: Path) -> str | None:
    executable = shutil.which("git")
    if executable is None:
        return None
    try:
        executable_path = Path(executable).resolve(strict=True)
        if executable_path.is_relative_to(root):
            return None
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("GIT_")}
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                           GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
        result = subprocess.run(
            [str(executable_path), "--no-pager", "--no-optional-locks",
             "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}",
             "-C", str(root), "rev-parse", "--verify", "HEAD"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="ascii", errors="strict", check=False, timeout=3.0, env=environment,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return None
    head = result.stdout.strip()
    return head if result.returncode == 0 and _HEAD.fullmatch(head) else None


def _make_dirs(path: Path, created: list[Path]) -> None:
    checked = _safe_path(path)
    kind = _kind(checked, missing_ok=True)
    if kind == "directory":
        return
    if kind != "missing":
        raise ValueError(f"El padre de salida no es un directorio: {checked}")
    _make_dirs(checked.parent, created)
    checked.mkdir()
    created.append(checked)


def _write_exclusive(output: Path, payload: dict[str, Any]) -> None:
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    created_dirs: list[Path] = []
    created_file = False
    try:
        _make_dirs(output.parent, created_dirs)
        _safe_path(output)
        with output.open("xb") as stream:
            created_file = True
            stream.write(data)
    except (OSError, ValueError) as exc:
        cleanup_errors: list[str] = []
        if created_file:
            try:
                output.unlink()
            except OSError as cleanup:
                cleanup_errors.append(str(cleanup))
        for path in reversed(created_dirs):
            try:
                path.rmdir()
            except OSError as cleanup:
                cleanup_errors.append(str(cleanup))
        if cleanup_errors:
            exc.add_note("Limpieza incompleta: " + "; ".join(cleanup_errors))
        raise


def snapshot(root: Path, sources: Sequence[str], documents: Sequence[str], output: Path,
             *, discover_roots: Sequence[str] | None = None) -> dict[str, Any]:
    """Create a new, exclusive snapshot; callers control write authorization."""
    directory = _root(root)
    policy = None
    if discover_roots is not None:
        from discovery import make_policy
        policy = make_policy(directory, discover_roots)
    source_paths = _selected(sources)
    document_paths = _selected(documents, documents=True)
    destination = _safe_path(output)
    if policy is not None:
        from discovery import outside_discovery
        outside_discovery(destination, directory, policy)
    scopes = [{"path": path, "kind": _kind(directory / path)} for path in source_paths]
    _outside_observed(destination, directory, scopes, document_paths)
    if _identity(destination) is not None:
        raise FileExistsError(f"El snapshot debe ser nuevo: {destination}")
    inventory_before = None
    if policy is not None:
        from discovery import worktree_inventory
        inventory_before = worktree_inventory(directory, policy)
    files, directories, kinds = _collect_sources(directory, scopes)
    if any(kinds[scope["path"]] != scope["kind"] for scope in scopes):
        raise OSError("Un alcance cambió de tipo durante la captura.")
    docs = _collect_documents(directory, document_paths, files)
    payload = {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION, "kit_version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "hash_algorithm": "sha256", "semantic_review": "not_attested",
        "exclusions": list(EXCLUSIONS), "source_scopes": scopes,
        "source_directories": sorted(directories),
        "sources": [files[path] for path in sorted(files)],
        "documents": [docs[path] for path in sorted(docs)],
        "git_head": _git_head(directory), "coverage": COVERAGE,
    }
    if policy is not None:
        payload["schema_version"] = 2
        payload["discovery"] = policy
    _validate_manifest(payload)
    _outside_observed(_safe_path(destination), directory, scopes, document_paths)
    if policy is not None:
        outside_discovery(destination, directory, policy)
        if worktree_inventory(directory, policy) != inventory_before:
            raise OSError("El inventario Git cambió durante la captura; reintenta.")
    _write_exclusive(destination, payload)
    return payload


def _object(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"Esquema inválido en {label}: campos ausentes o desconocidos.")
    return value


def _records(value: object, label: str, *, required: bool = False) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list) or (required and not value):
        raise ValueError(f"Esquema inválido: {label} debe ser una lista de archivos.")
    records: dict[str, dict[str, Any]] = {}
    for item in value:
        entry = _object(item, {"path", "size", "sha256"}, label)
        relative = _relative(entry["path"], canonical=True, allow_root=False)
        if (type(entry["size"]) is not int or entry["size"] < 0
                or not isinstance(entry["sha256"], str) or not _HASH.fullmatch(entry["sha256"])):
            raise ValueError(f"Tamaño o SHA-256 inválido en {label}: {relative}")
        if relative in records:
            raise ValueError(f"Ruta duplicada en {label}: {relative}")
        records[relative] = entry
    return records


def _within(path: str, directory: str) -> bool:
    return directory == "." or path == directory or path.startswith(directory + "/")


def _validate_manifest(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int:
        raise ValueError("Versión de esquema inválida.")
    schema_version = value["schema_version"]
    extra = {"discovery"} if schema_version == 2 else set()
    payload = _object(value, {
        "schema", "schema_version", "kit_version", "created_at", "hash_algorithm",
        "semantic_review", "exclusions", "source_scopes", "source_directories",
        "sources", "documents", "git_head", "coverage",
    } | extra, "snapshot")
    if (payload["schema"] != SCHEMA or type(payload["schema_version"]) is not int
            or payload["schema_version"] not in (1, 2)
            or not isinstance(payload["kit_version"], str)
            or not _KIT_VERSION.fullmatch(payload["kit_version"])
            or payload["hash_algorithm"] != "sha256"
            or payload["semantic_review"] != "not_attested"
            or payload["exclusions"] != list(EXCLUSIONS) or payload["coverage"] != COVERAGE):
        raise ValueError("Versión, política o declaración del snapshot desconocida.")
    if schema_version == 2:
        from discovery import validate_policy
        validate_policy(payload["discovery"])
    created_at = payload["created_at"]
    try:
        if not isinstance(created_at, str):
            raise ValueError("La fecha debe ser texto UTC.")
        parsed = datetime.fromisoformat(created_at)
        canonical = parsed.isoformat(timespec="seconds").replace("+00:00", "Z")
        if not created_at.endswith("Z") or created_at != canonical:
            raise ValueError("La fecha debe ser UTC canónica.")
    except (ValueError, OverflowError) as exc:
        raise ValueError("Fecha UTC inválida en el snapshot.") from exc
    if payload["git_head"] is not None and (
            not isinstance(payload["git_head"], str) or not _HEAD.fullmatch(payload["git_head"])):
        raise ValueError("Git HEAD inválido en el snapshot.")
    scope_list = payload["source_scopes"]
    if not isinstance(scope_list, list) or not scope_list:
        raise ValueError("El snapshot requiere al menos un alcance de fuentes.")
    scopes: dict[str, str] = {}
    for item in scope_list:
        scope = _object(item, {"path", "kind"}, "alcances")
        relative = _relative(scope["path"], canonical=True)
        if scope["kind"] not in ("file", "directory") or relative in scopes:
            raise ValueError("Alcance duplicado o tipo de alcance inválido.")
        if relative == "." and scope["kind"] != "directory":
            raise ValueError("La raíz solo puede ser un alcance de directorio.")
        scopes[relative] = scope["kind"]
    files = _records(payload["sources"], "sources")
    docs = _records(payload["documents"], "documents", required=True)
    directory_list = payload["source_directories"]
    if not isinstance(directory_list, list):
        raise ValueError("source_directories debe ser una lista.")
    directories = {_relative(path, canonical=True) for path in directory_list}
    if len(directories) != len(directory_list):
        raise ValueError("Directorio duplicado en el snapshot.")
    directory_scopes = {path for path, kind in scopes.items() if kind == "directory"}
    for path, kind in scopes.items():
        if path not in (directories if kind == "directory" else files):
            raise ValueError(f"El snapshot omite la evidencia de su alcance: {path}")
    for path in files:
        if scopes.get(path) != "file" and not any(_within(path, scope) for scope in directory_scopes):
            raise ValueError(f"Archivo fuera de los alcances declarados: {path}")
    for path in directories:
        if not any(_within(path, scope) for scope in directory_scopes):
            raise ValueError(f"Directorio fuera de los alcances declarados: {path}")
    for path in docs:
        if any(_within(path, scope) for scope in directory_scopes) and path not in files:
            raise ValueError(f"Documento observado omitido del inventario de fuentes: {path}")
    all_files = set(files) | set(docs)
    if all_files & directories:
        raise ValueError("Una ruta no puede ser archivo y directorio en el snapshot.")
    for path in all_files | directories:
        parents = [parent.as_posix() for parent in PurePosixPath(path).parents]
        if any(parent in all_files for parent in parents):
            raise ValueError("Un archivo del snapshot no puede ser padre de otra ruta.")
        if path in files or path in directories:
            for parent in parents:
                if (any(_within(parent, scope) for scope in directory_scopes)
                        and parent not in directories):
                    raise ValueError(f"Falta un directorio intermedio observado: {parent}")
    for path in files.keys() & docs.keys():
        if files[path] != docs[path]:
            raise ValueError(f"Evidencia contradictoria para fuente y documento: {path}")
    return payload


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Clave JSON duplicada: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError(f"Constante JSON no permitida: {value}")


def _load_manifest(path: Path) -> dict[str, Any]:
    chunks: list[bytes] = []
    _fingerprint(path, "snapshot", chunks=chunks)
    try:
        payload = json.loads(b"".join(chunks).decode("utf-8"),
                             object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except RecursionError as exc:
        raise ValueError("Snapshot JSON demasiado anidado.") from exc
    return _validate_manifest(payload)


def _diff(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    return {
        "added": sorted(after.keys() - before.keys()),
        "modified": sorted(path for path in before.keys() & after.keys()
                           if before[path] != after[path]),
        "deleted": sorted(before.keys() - after.keys()),
    }


def status(root: Path, snapshot_path: Path, *, knowledge_map: str | None = None) -> dict[str, Any]:
    """Compare current bytes to a validated snapshot without writing to ROOT."""
    directory = _root(root)
    baseline_path = _safe_path(snapshot_path)
    knowledge = None
    if knowledge_map is not None:
        if not baseline_path.is_relative_to(directory):
            raise ValueError('unsupported_external_snapshot')
        from doc_evidence import open_view
        reader = open_view(directory, 'worktree')
        relative_snapshot = baseline_path.relative_to(directory).as_posix()
        reader.fingerprint(relative_snapshot)
    baseline = _load_manifest(baseline_path)
    if knowledge_map is not None:
        for item in baseline['sources'] + baseline['documents']:
            reader.fingerprint(item['path'])
    policy = baseline.get("discovery")
    inventory_before = None
    if policy is not None:
        from discovery import outside_discovery, worktree_inventory
        outside_discovery(baseline_path, directory, policy)
        inventory_before = worktree_inventory(directory, policy)
    scopes = baseline["source_scopes"]
    document_paths = [item["path"] for item in baseline["documents"]]
    _outside_observed(baseline_path, directory, scopes, document_paths)
    baseline_identity = _identity(baseline_path)
    files, directories, kinds = _collect_sources(directory, scopes, missing_ok=True)
    if baseline_identity is not None and any(
            _identity(directory / path) == baseline_identity for path in files):
        raise ValueError("Una fuente observada es un alias del archivo snapshot.")
    documents = _collect_documents(directory, document_paths, files, missing_ok=True)
    source_changes = _diff({item["path"]: item for item in baseline["sources"]}, files)
    document_changes = _diff({item["path"]: item for item in baseline["documents"]}, documents)
    scope_changes = [
        {"path": scope["path"], "before": scope["kind"], "after": kinds[scope["path"]]}
        for scope in sorted(scopes, key=lambda item: item["path"])
        if scope["kind"] != kinds[scope["path"]]
    ]
    old_directories = set(baseline["source_directories"])
    directory_changes = {"added": sorted(directories - old_directories),
                         "deleted": sorted(old_directories - directories)}
    coverage_report = None
    if policy is not None:
        from discovery import report as discovery_report
        inventory_after = worktree_inventory(directory, policy)
        if inventory_before != inventory_after:
            raise OSError("El inventario Git cambió durante la comprobación; reintenta.")
        covered = (set(files) | set(documents)
                   | {x["path"] for x in baseline["sources"] + baseline["documents"]})
        coverage_report = discovery_report(policy, "worktree", inventory_after[2], covered)
    changed = bool((coverage_report and coverage_report["state"] == "uncovered")
                   or scope_changes or any(source_changes.values())
                   or any(document_changes.values()) or any(directory_changes.values()))
    if knowledge_map is not None:
        from doc_impact import check_knowledge
        knowledge = check_knowledge(directory, knowledge_map, relative_snapshot, read_view=reader)
        reader.assert_unchanged()
        changed = changed or knowledge['state'] != 'no_linked_change'
    return {
        "state": "review_required" if changed else "unchanged_since_snapshot",
        **({"knowledge": knowledge} if knowledge is not None else {}),
        "kit_version": VERSION, "snapshot_created_at": baseline["created_at"],
        "semantic_review": "not_attested", "sources": source_changes,
        "documents": document_changes, "scope_changes": scope_changes,
        "directory_changes": directory_changes,
        "git": {"snapshot_head": baseline["git_head"], "current_head": _git_head(directory)},
        "coverage": COVERAGE, "coverage_note": COVERAGE_NOTE,
        **({"coverage_check": coverage_report} if coverage_report is not None else {}),
    }
