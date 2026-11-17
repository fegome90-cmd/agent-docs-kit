"""Independent, cooperative append-only session history.

The Summary/Files/Commands entry template in ``_render_entry`` is adapted from
Trifecta ``src/infrastructure/cli.py::session_append``, commit
5399a7f47a5f3121c3855225e236907a184d270b. The JSONL storage, validation, locking and
idempotency implementation is new. No Trifecta runtime is imported. JSONL is the
source of record; Markdown is a read-only derived view, not semantic approval.

Locks coordinate participating writers on a filesystem that supports locking.
They do not prevent direct edits by other programs. Readers never create locks
or directories, and detect observed concurrent changes rather than claiming an
atomic snapshot. Corrupt/incomplete segments are never repaired automatically.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import stat
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, BinaryIO
from uuid import UUID, uuid4

SCHEMA_VERSION = 1
DEFAULT_HISTORY = "docs/living/history"
MAX_EVENT_BYTES = 1024 * 1024
MAX_SEGMENT_BYTES = 64 * 1024 * 1024
MAX_JSON_DEPTH = 64
_FIELDS = frozenset(
    {
        "schema_version",
        "event_id",
        "idempotency_key",
        "session_id",
        "sequence",
        "occurred_at",
        "kind",
        "source",
        "payload",
    }
)
_KIND = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*\Z")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")


def _uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} debe ser un UUID canónico.")
    try:
        normalized = str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{label} debe ser un UUID canónico.") from exc
    if normalized != value:
        raise ValueError(f"{label} debe ser un UUID canónico en minúsculas.")
    return value


def _text(value: object, label: str, limit: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > limit
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError(f"{label} debe ser texto no vacío, sin controles, de hasta {limit} caracteres.")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError(f"{label} contiene Unicode inválido.") from exc
    return value


def _kind(value: object) -> str:
    result = _text(value, "kind", 64)
    if not _KIND.fullmatch(result):
        raise ValueError("kind debe ser un identificador minúsculo, como 'work.logged' o 'session.closed'.")
    return result


def _json_value(value: Any, depth: int = 0) -> None:
    if depth > MAX_JSON_DEPTH:
        raise ValueError(f"El payload supera la profundidad máxima de {MAX_JSON_DEPTH}.")
    if value is None or type(value) in (bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("JSON no admite valores numéricos no finitos.")
        return
    if type(value) is str:
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ValueError("El payload contiene Unicode inválido.") from exc
        return
    if type(value) is list:
        for child in value:
            _json_value(child, depth + 1)
        return
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise ValueError("Todas las claves del payload deben ser strings.")
            _json_value(key, depth + 1)
            _json_value(child, depth + 1)
        return
    raise ValueError(f"Tipo no permitido en JSON: {type(value).__name__}.")


def _payload(value: object) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError("payload debe ser un objeto JSON.")
    _json_value(value)
    return value


def _canonical(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("No se puede serializar el evento como JSON estricto.") from exc


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON contiene una clave duplicada.")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError(f"Constante JSON no permitida: {value}.")


def _event(value: object, expected_session: str | None) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _FIELDS:
        raise ValueError("El evento debe contener exactamente los nueve campos del contrato.")
    if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("schema_version no es compatible.")
    _uuid(value["event_id"], "event_id")
    session = _uuid(value["session_id"], "session_id")
    if expected_session is not None and session != expected_session:
        raise ValueError("session_id no coincide con el segmento.")
    _text(value["idempotency_key"], "idempotency_key")
    if type(value["sequence"]) is not int or value["sequence"] < 1:
        raise ValueError("sequence debe ser un entero positivo, nunca un booleano.")
    timestamp = value["occurred_at"]
    if not isinstance(timestamp, str) or not _TIMESTAMP.fullmatch(timestamp):
        raise ValueError("occurred_at debe ser una fecha ISO 8601 UTC terminada en Z.")
    try:
        datetime.fromisoformat(timestamp[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("occurred_at contiene una fecha inválida.") from exc
    _kind(value["kind"])
    _text(value["source"], "source", 128)
    _payload(value["payload"])
    return value


def parse_segment(data: bytes, expected_session: str | None = None) -> list[dict[str, Any]]:
    """Validate a complete UTF-8 JSONL segment without filesystem access.

    Empty bytes form a valid empty prefix. Nonempty input must end with LF;
    blank rows, duplicate keys/IDs, nonfinite numbers, gaps, mixed sessions and
    rows after ``session.closed`` are errors. No recovery or normalization of
    the original bytes is performed.
    """
    if not isinstance(data, bytes):
        raise ValueError("El segmento debe entregarse como bytes.")
    if expected_session is not None:
        _uuid(expected_session, "session_id")
    if len(data) > MAX_SEGMENT_BYTES:
        raise ValueError(f"El segmento supera {MAX_SEGMENT_BYTES} bytes.")
    if not data:
        return []
    if not data.endswith(b"\n"):
        raise ValueError("El segmento tiene una cola incompleta: falta el salto de línea final.")
    events: list[dict[str, Any]] = []
    ids: set[str] = set()
    keys: set[str] = set()
    session = expected_session
    for index, row in enumerate(data.split(b"\n")[:-1], 1):
        if len(row) + 1 > MAX_EVENT_BYTES:
            raise ValueError(f"El evento {index} supera {MAX_EVENT_BYTES} bytes.")
        if not row:
            raise ValueError(f"El segmento contiene una fila vacía en {index}.")
        try:
            decoded = row.decode("utf-8", errors="strict")
            value = json.loads(decoded, object_pairs_hook=_pairs, parse_constant=_constant)
            event = _event(value, session)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ValueError(f"Evento inválido en fila {index}: {exc}") from exc
        session = event["session_id"]
        if event["sequence"] != index:
            raise ValueError(f"Secuencia discontinua en la fila {index}.")
        if event["event_id"] in ids:
            raise ValueError(f"event_id duplicado en la fila {index}.")
        if event["idempotency_key"] in keys:
            raise ValueError(f"idempotency_key duplicada en la fila {index}.")
        if events and events[-1]["kind"] == "session.closed":
            raise ValueError("Un segmento cerrado no admite eventos posteriores.")
        ids.add(event["event_id"])
        keys.add(event["idempotency_key"])
        events.append(event)
    return events


def _safe_path(path: Path) -> Path:
    absolute = Path(path).expanduser()
    if not absolute.is_absolute():
        absolute = Path.cwd() / absolute
    cursor = Path(absolute.anchor)
    for index, part in enumerate(absolute.parts[1:], 1):
        cursor = cursor.parent if part == ".." else cursor / part
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            continue
        except NotADirectoryError as exc:
            raise ValueError(f"Un componente de la ruta no es un directorio: {cursor}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise ValueError(f"Enlace simbólico no permitido: {cursor}")
        if index < len(absolute.parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"Un componente de la ruta no es un directorio: {cursor}")
    return cursor


def _location(root: Path, history: str) -> tuple[Path, Path, str]:
    root = _safe_path(root)
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise ValueError("La raíz debe ser un directorio existente.")
    value = _text(history, "history", 4096)
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or PureWindowsPath(value).drive
        or "\\" in value
        or ".." in relative.parts
        or relative.as_posix() != value
        or value == "."
    ):
        raise ValueError("history debe ser una ruta relativa canónica dentro de la raíz.")
    directory = _safe_path(root / value)
    if not directory.is_relative_to(root):
        raise ValueError("history debe quedar dentro de la raíz.")
    try:
        info = directory.lstat()
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("history debe ser un directorio.")
    return root, directory, value


def _mkdirs(root: Path, directory: Path) -> None:
    cursor = root
    for part in directory.relative_to(root).parts:
        cursor = _safe_path(cursor / part)
        with contextlib.suppress(FileExistsError):
            cursor.mkdir()
        if not stat.S_ISDIR(_safe_path(cursor).lstat().st_mode):
            raise ValueError(f"El padre del segmento no es un directorio: {cursor}")


def _regular(path: Path, *, missing_ok: bool = False) -> os.stat_result | None:
    checked = _safe_path(path)
    try:
        info = checked.lstat()
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Se requiere un archivo regular: {path}")
    if info.st_nlink != 1:
        raise ValueError(f"No se permiten archivos con enlaces duros: {path}")
    return info


def _signature(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _check_handle(path: Path, descriptor: int, expected: os.stat_result | None = None) -> os.stat_result:
    actual = os.fstat(descriptor)
    if not stat.S_ISREG(actual.st_mode) or actual.st_nlink != 1:
        raise ValueError(f"Se requiere un archivo regular sin enlaces: {path}")
    current = _regular(path)
    if (
        current is None
        or _signature(current) != _signature(actual)
        or (expected is not None and _signature(expected) != _signature(actual))
    ):
        raise OSError(f"El archivo cambió durante la operación: {path}")
    return actual


def _open_file(path: Path, flags: int, *, create: bool = False) -> int:
    """Open without truncation, reject observed aliases, and check the handle."""
    before = _regular(path, missing_ok=create)
    extra = getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    extra |= getattr(os, "O_BINARY", 0)
    flags &= ~os.O_TRUNC
    flags &= ~os.O_CREAT
    descriptor: int
    if before is None:
        try:
            descriptor = os.open(path, flags | extra | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            # Another cooperating writer may just have created the sidecar.
            before = _regular(path)
            descriptor = os.open(path, flags | extra)
    else:
        descriptor = os.open(path, flags | extra)
    try:
        actual = _check_handle(path, descriptor)
        if before is not None and (before.st_dev, before.st_ino) != (actual.st_dev, actual.st_ino):
            raise OSError(f"El archivo fue sustituido antes de abrirlo: {path}")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _read_open(stream: BinaryIO, path: Path) -> tuple[bytes, os.stat_result]:
    before = _check_handle(path, stream.fileno())
    if before.st_size > MAX_SEGMENT_BYTES:
        raise ValueError(f"El segmento supera {MAX_SEGMENT_BYTES} bytes.")
    stream.seek(0)
    data = stream.read(MAX_SEGMENT_BYTES + 1)
    after = _check_handle(path, stream.fileno(), before)
    if len(data) != before.st_size:
        raise OSError(f"Lectura incompleta o concurrente del segmento: {path}")
    return data, after


def _read_file(path: Path) -> bytes:
    descriptor = _open_file(path, os.O_RDONLY)
    with os.fdopen(descriptor, "rb") as stream:
        data, _ = _read_open(stream, path)
    return data


def _sync(stream: BinaryIO) -> None:
    stream.flush()
    os.fsync(stream.fileno())


def append_event(
    root: Path,
    history: str,
    session: str,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str,
    source: str = "cli",
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Append one event under a persistent sidecar lock and fsync before ACK.

    An identical retry returns the original event and fsyncs again, including
    after closure. Reusing a key with different kind/source/payload is an error.
    After an I/O failure the caller must treat the outcome as unknown and retry
    with the same key: this function never truncates, rolls back or repairs.
    """
    session = _uuid(session, "session_id")
    kind = _kind(kind)
    idempotency_key = _text(idempotency_key, "idempotency_key")
    source = _text(source, "source", 128)
    payload_json = _canonical(_payload(payload))
    if len(payload_json.encode("utf-8")) > MAX_EVENT_BYTES:
        raise ValueError(f"El payload supera el límite de evento de {MAX_EVENT_BYTES} bytes.")
    frozen_payload = json.loads(payload_json)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout < 0:
        raise ValueError("timeout debe ser un número finito mayor o igual que cero.")
    root, directory, _ = _location(root, history)
    path = directory / f"{session}.jsonl"
    lock_path = directory / f"{session}.jsonl.lock"
    _regular(path, missing_ok=True)
    _regular(lock_path, missing_ok=True)
    try:
        import portalocker
    except ImportError as exc:
        raise OSError("append requiere la dependencia opcional portalocker==4.4.2.") from exc
    _mkdirs(root, directory)

    def opener(filename: str, flags: int) -> int:
        return _open_file(Path(filename), flags | os.O_APPEND, create=True)

    lock = portalocker.Lock(
        str(lock_path),
        mode="ab",
        timeout=timeout,
        check_interval=0.05,
        opener=opener,
        raise_on_release_error=True,
    )
    try:
        with lock as lock_stream:
            _check_handle(lock_path, lock_stream.fileno())
            descriptor = _open_file(path, os.O_RDWR | os.O_APPEND, create=True)
            with os.fdopen(descriptor, "a+b") as stream:
                data, before = _read_open(stream, path)
                events = parse_segment(data, expected_session=session)
                for existing in events:
                    if existing["idempotency_key"] == idempotency_key:
                        if (
                            existing["kind"] != kind
                            or existing["source"] != source
                            or _canonical(existing["payload"]) != payload_json
                        ):
                            raise ValueError("Conflicto: idempotency_key ya existe con otro contenido.")
                        _check_handle(path, stream.fileno(), before)
                        _sync(stream)
                        _check_handle(path, stream.fileno(), before)
                        _check_handle(lock_path, lock_stream.fileno())
                        return existing
                if events and events[-1]["kind"] == "session.closed":
                    raise ValueError("La sesión está cerrada; sólo se admite reintentar un evento idéntico.")
                event = {
                    "schema_version": SCHEMA_VERSION,
                    "event_id": str(uuid4()),
                    "idempotency_key": idempotency_key,
                    "session_id": session,
                    "sequence": len(events) + 1,
                    "occurred_at": datetime.now(UTC)
                    .isoformat(timespec="microseconds")
                    .replace("+00:00", "Z"),
                    "kind": kind,
                    "source": source,
                    "payload": frozen_payload,
                }
                line = (_canonical(event) + "\n").encode("utf-8")
                if len(line) > MAX_EVENT_BYTES or len(data) + len(line) > MAX_SEGMENT_BYTES:
                    raise ValueError("El append excedería el tamaño máximo del evento o segmento.")
                _check_handle(path, stream.fileno(), before)
                stream.seek(0, os.SEEK_END)
                if stream.write(line) != len(line):
                    raise OSError("Escritura incompleta; no se confirmó el evento.")
                _sync(stream)
                after = _check_handle(path, stream.fileno())
                if after.st_size != len(data) + len(line):
                    raise OSError("El tamaño del segmento cambió durante el append.")
                _check_handle(lock_path, lock_stream.fileno())
                return event
    except portalocker.exceptions.AlreadyLocked as exc:
        raise TimeoutError(f"Tiempo agotado al adquirir el lock de la sesión ({timeout}s).") from exc
    except portalocker.exceptions.LockException as exc:
        raise OSError(f"Falló el lock cooperativo de la sesión: {exc}") from exc


def _load(
    root: Path, history: str, session: str | None
) -> tuple[str, list[tuple[Path, list[dict[str, Any]]]]]:
    root, directory, relative = _location(root, history)
    if session is not None:
        session = _uuid(session, "session_id")
        names = [f"{session}.jsonl"]
    else:
        before = directory.stat()
        with os.scandir(directory) as iterator:
            names = sorted(entry.name for entry in iterator if entry.name.endswith(".jsonl"))
        if _signature(before) != _signature(_safe_path(directory).stat()):
            raise OSError("El directorio de historial cambió durante la enumeración.")
    segments: list[tuple[Path, list[dict[str, Any]]]] = []
    ids: set[str] = set()
    for name in names:
        expected = _uuid(name[:-6], "Nombre de segmento")
        path = directory / name
        events = parse_segment(_read_file(path), expected_session=expected)
        for event in events:
            if event["event_id"] in ids:
                raise ValueError("event_id duplicado entre sesiones.")
            ids.add(event["event_id"])
        segments.append((path.relative_to(root), events))
    return relative, segments


def verify_history(root: Path, history: str, session: str | None = None) -> dict[str, Any]:
    """Read and validate selected segments, without writes or lock creation."""
    relative, segments = _load(root, history, session)
    summaries = [
        {
            "session_id": path.stem,
            "path": path.as_posix(),
            "state": "closed" if events and events[-1]["kind"] == "session.closed" else "open",
            "event_count": len(events),
            "last_sequence": len(events),
        }
        for path, events in segments
    ]
    return {
        "state": "valid",
        "history": relative,
        "sessions": summaries,
        "event_count": sum(item["event_count"] for item in summaries),
        "semantic_review": "not_attested",
    }


def _code(value: str) -> str:
    fence = "`" * max(1, max((len(run) for run in re.findall(r"`+", value)), default=0) + 1)
    return f"{fence} {value} {fence}"


def _markdown_text(value: str) -> str:
    # Keep each adapted label on one physical line; full strings remain in JSON.
    value = "".join(f"\\u{ord(char):04x}" if ord(char) < 32 or ord(char) == 127 else char for char in value)
    value = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]()#+.!|~-])", r"\\\1", value)


def _render_entry(event: dict[str, Any]) -> list[str]:
    """Adapt Trifecta's entry_lines template, without its original file writer."""
    payload = event["payload"]
    entry_lines = [f"### {event['sequence']}. {event['kind']} — {event['occurred_at']}", ""]
    summary = payload.get("summary")
    files_list = payload.get("files")
    commands_list = payload.get("commands")
    if isinstance(summary, str):
        entry_lines.append(f"- **Summary**: {_markdown_text(summary)}")
    if isinstance(files_list, list) and files_list and all(isinstance(item, str) for item in files_list):
        entry_lines.append(f"- **Files**: {', '.join(_markdown_text(item) for item in files_list)}")
    if (
        isinstance(commands_list, list)
        and commands_list
        and all(isinstance(item, str) for item in commands_list)
    ):
        entry_lines.append(f"- **Commands**: {', '.join(_markdown_text(item) for item in commands_list)}")
    entry_lines.extend(
        [
            f"- Event ID: {_code(event['event_id'])}",
            f"- Source: {_code(event['source'])}",
            f"- Idempotency key: {_code(event['idempotency_key'])}",
            "",
        ]
    )
    content = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)
    fence = "`" * max(3, max((len(run) for run in re.findall(r"`+", content)), default=0) + 1)
    entry_lines.extend([f"{fence}json", content, fence, ""])
    return entry_lines


def render_history(root: Path, history: str, session: str | None = None) -> str:
    """Return a derived Markdown view; never mutate or export source files."""
    _, segments = _load(root, history, session)
    lines = [
        "# Session History",
        "",
        "Semantic review: `not_attested`.",
        "",
        "Vista derivada de eventos JSONL validados; no certifica corrección semántica.",
        "",
    ]
    if not segments:
        lines.extend(["No hay sesiones registradas.", ""])
    for path, events in segments:
        closed = bool(events and events[-1]["kind"] == "session.closed")
        lines.extend(
            [
                f"## Session {path.stem}",
                "",
                f"- State: {'closed' if closed else 'open'}",
                f"- Events: {len(events)}",
                "",
            ]
        )
        for event in events:
            lines.extend(_render_entry(event))
    return "\n".join(lines)
