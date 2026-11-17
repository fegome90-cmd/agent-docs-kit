"""Read-only, exact comparisons for explicitly declared documentation facts.

Reuse notice: ``_extract_details`` and ``extract`` are adapted from
conorbronsdon/ssot-check, ``ssot_check.py``, commit
5106ac6b22daad7e37d39139f6261094549df184 (Copyright (c) 2026 Conor Bronsdon).
Source: https://github.com/conorbronsdon/ssot-check/blob/
5106ac6b22daad7e37d39139f6261094549df184/ssot_check.py
MIT license: licenses/ssot-check-MIT.txt. The adaptation requires exactly one
capture group, refuses to select an ambiguous first match, and preserves the
captured string without normalization. YAML, remote reads, discovery, rounding,
freshness, and automatic updates from upstream are not included.

Patterns are trusted Python ``re`` configuration. A one-MiB input limit does
not provide a regex execution timeout. No inspected project code is executed,
no files are updated, and a successful comparison does not attest semantics.
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

MAX_FILE_BYTES = 1024 * 1024


def _text(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{where}: se requiere texto UTF-8.")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError(f"{where}: texto UTF-8 inválido.") from exc
    if len(encoded) > MAX_FILE_BYTES:
        raise ValueError(f"{where}: supera el límite de 1 MiB.")
    return value


def _relative_file(value: object) -> str:
    text = _text(value, "file")
    if not text or "\x00" in text or "\\" in text:
        raise ValueError(f"Ruta relativa inválida: {value!r}")
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or PureWindowsPath(text).drive
        or ".." in path.parts
        or path.as_posix() != text
        or text == "."
    ):
        raise ValueError(f"Se requiere una ruta POSIX relativa canónica: {value!r}")
    return text


def _compile(pattern: object, where: str) -> re.Pattern[str]:
    text = _text(pattern, where)
    try:
        compiled = re.compile(text)
    except (re.error, RecursionError, OverflowError) as exc:
        raise ValueError(f"{where}: expresión regular inválida ({exc}).") from exc
    if compiled.groups != 1:
        raise ValueError(
            f"{where}: pattern debe tener exactamente un grupo de captura (encontrados: {compiled.groups})."
        )
    return compiled


def _mapping(value: object, keys: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{where}: se requieren exactamente las claves {sorted(keys)}.")
    return value


def _locator(value: object, where: str) -> tuple[str, str]:
    locator = _mapping(value, {"file", "pattern"}, where)
    filename = _relative_file(locator["file"])
    _compile(locator["pattern"], f"{where}.pattern")
    return filename, locator["pattern"]


def validate_manifest(manifest: object) -> None:
    """Validate the complete schema before reading any declared file.

    Only schema version 1 is supported. Invalid configuration raises ValueError;
    empty checks, duplicate IDs, and identical canonical/copy locators cannot
    produce a successful report.
    """
    payload = _mapping(manifest, {"schema_version", "facts"}, "manifest")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("manifest.schema_version debe ser el entero 1.")
    facts = payload["facts"]
    if not isinstance(facts, list) or not facts:
        raise ValueError("manifest.facts debe ser una lista no vacía.")
    seen_ids: set[str] = set()
    for index, value in enumerate(facts):
        where = f"facts[{index}]"
        fact = _mapping(value, {"id", "canonical", "copies"}, where)
        identifier = _text(fact["id"], f"{where}.id")
        if not identifier or identifier != identifier.strip():
            raise ValueError(f"{where}.id debe ser texto no vacío sin espacios exteriores.")
        if identifier in seen_ids:
            raise ValueError(f"ID de hecho duplicado: {identifier!r}")
        seen_ids.add(identifier)
        canonical = _locator(fact["canonical"], f"{where}.canonical")
        copies = fact["copies"]
        if not isinstance(copies, list) or not copies:
            raise ValueError(f"{where}.copies debe ser una lista no vacía.")
        seen_locators = {canonical}
        for copy_index, copy in enumerate(copies):
            locator = _locator(copy, f"{where}.copies[{copy_index}]")
            if locator in seen_locators:
                raise ValueError(f"{where}: ubicación de copia duplicada o igual a canonical.")
            seen_locators.add(locator)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Clave JSON duplicada: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"Constante no permitida en JSON: {value}")


def parse_manifest(text: str) -> dict[str, Any]:
    """Parse strict UTF-8 JSON, including duplicate-key rejection at every level."""
    _text(text, "manifest")
    try:
        manifest = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"JSON de manifiesto inválido: {exc}") from exc
    validate_manifest(manifest)
    return manifest


def _extract_details(
    content: str, pattern: str
) -> tuple[str | None, int | None, int, int | None, int | None]:
    """Adapted ssot-check extraction; an ambiguous capture has no chosen value."""
    compiled = _compile(pattern, "pattern")
    matches = compiled.finditer(content)
    first = next(matches, None)
    if first is None:
        return None, None, 0, None, None
    count = 1 + sum(1 for _ in matches)
    if count != 1:
        return None, None, count, None, None
    start, end = first.span(1)
    if start < 0:  # An optional capture did not participate in the match.
        return None, None, 1, None, None
    lineno = content.count("\n", 0, start) + 1
    return first.group(1), lineno, count, start, end


def extract(content: str, pattern: str) -> tuple[str | None, int | None, int]:
    """Return the exact unique capture, its line, and the total match count.

    Adapted from ssot-check.extract. Patterns apply to the whole text, and the
    line identifies the captured group rather than the beginning of the match.
    Missing, nonparticipating, and ambiguous captures return no value or line.
    """
    value, line, count, _, _ = _extract_details(content, pattern)
    return value, line, count


def check_manifest(manifest: dict[str, Any], read_text: Callable[[str], str | None]) -> dict[str, Any]:
    """Compare a manifest using an explicit read-only content provider.

    ``read_text`` receives validated root-relative POSIX filenames and returns
    UTF-8 text or None when a file is missing. It must reject symlinks/nonregular
    files and must not execute project code. Git callers may supply index blobs
    instead of working-tree content. Read errors must be raised, never mapped
    to empty text. Each declared file is read at most once per invocation.
    """
    validate_manifest(manifest)
    if not callable(read_text):
        raise ValueError("read_text debe ser una función de lectura explícita.")
    cache: dict[str, str | None] = {}
    spans: dict[tuple[str, str], tuple[int, int] | None] = {}

    def observe(locator: dict[str, str]) -> dict[str, Any]:
        filename = locator["file"]
        if filename not in cache:
            content = read_text(filename)
            cache[filename] = None if content is None else _text(content, filename)
        content = cache[filename]
        evidence: dict[str, Any] = {
            "file": filename,
            "line": None,
            "value": None,
            "match_count": 0,
            "reason": "missing_file",
        }
        if content is not None:
            value, line, count, start, end = _extract_details(content, locator["pattern"])
            spans[(filename, locator["pattern"])] = (
                (start, end) if start is not None and end is not None else None
            )
            reason = (
                "missing_match"
                if count == 0
                else "ambiguous"
                if count > 1
                else "missing_capture"
                if value is None
                else "captured"
            )
            evidence.update(line=line, value=value, match_count=count, reason=reason)
        return evidence

    facts: list[dict[str, Any]] = []
    for fact in manifest["facts"]:
        canonical = observe(fact["canonical"])
        if canonical["reason"] == "captured":
            canonical["reason"] = "canonical"
        copies = []
        for locator in fact["copies"]:
            copy = observe(locator)
            canonical_span = spans.get((fact["canonical"]["file"], fact["canonical"]["pattern"]))
            copy_span = spans.get((locator["file"], locator["pattern"]))
            if (
                locator["file"] == fact["canonical"]["file"]
                and canonical_span is not None
                and copy_span == canonical_span
            ):
                raise ValueError(f"{fact['id']}: canonical y copy capturan la misma ocurrencia.")
            if copy["reason"] == "captured":
                copy["reason"] = (
                    "canonical_unavailable"
                    if canonical["reason"] != "canonical"
                    else "match"
                    if copy["value"] == canonical["value"]
                    else "mismatch"
                )
            copies.append(copy)
        in_sync = canonical["reason"] == "canonical" and all(copy["reason"] == "match" for copy in copies)
        facts.append(
            {
                "id": fact["id"],
                "state": "in_sync" if in_sync else "review_required",
                "canonical": canonical,
                "copies": copies,
            }
        )
    return {
        "state": "in_sync" if all(fact["state"] == "in_sync" for fact in facts) else "review_required",
        "facts": facts,
        "semantic_review": "not_attested",
    }


def _read_regular_text(path: Path) -> str | None:
    # Reuse the kit's established path and read-stability checks. The import is
    # local so the pure manifest evaluator can also be used with an index reader.
    from living_docs import _safe_path, _signature

    checked = _safe_path(path)
    try:
        before = checked.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return None
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"Se requiere un archivo regular: {checked}")
    if before.st_size > MAX_FILE_BYTES:
        raise ValueError(f"{checked}: supera el límite de 1 MiB.")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(checked, flags)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or _signature(opened) != _signature(before):
            raise ValueError(f"El archivo cambió antes de leerlo: {checked}")
        data = stream.read(MAX_FILE_BYTES + 1)
        after = os.fstat(stream.fileno())
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"{checked}: supera el límite de 1 MiB.")
    current = _safe_path(checked).lstat()
    if (
        len(data) != opened.st_size
        or _signature(opened) != _signature(after)
        or _signature(after) != _signature(current)
    ):
        raise ValueError(f"El archivo cambió durante la lectura: {checked}")
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError(f"{checked}: el archivo no es UTF-8 válido.") from exc


def check_facts(root: Path, manifest: Path) -> dict[str, Any]:
    """Read a JSON manifest and its declared files inside an explicit root.

    Relative manifest paths are relative to ``root``. Absolute manifest paths
    must remain inside it. The working tree is read without modifying files.
    """
    from living_docs import _root, _safe_path

    project = _root(Path(root))
    manifest_path = Path(manifest)
    if ".." in manifest_path.parts:
        raise ValueError("La ruta del manifiesto no puede contener '..'.")
    if not manifest_path.is_absolute():
        manifest_path = project / _relative_file(manifest_path.as_posix())
    manifest_path = _safe_path(manifest_path)
    if not manifest_path.is_relative_to(project):
        raise ValueError("El manifiesto debe estar dentro de la raíz declarada.")
    text = _read_regular_text(manifest_path)
    if text is None:
        raise ValueError(f"No existe el manifiesto: {manifest_path}")
    payload = parse_manifest(text)
    return check_manifest(payload, lambda relative: _read_regular_text(project / relative))
