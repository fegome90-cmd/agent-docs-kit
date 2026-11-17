# /// script
# requires-python = ">=3.11"
# dependencies = ["portalocker==4.4.2"]
# ///
"""Prepare documentation drafts without replacing repository instructions."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

VERSION = "0.5.0-rc.1"

DOCUMENTS = (
    Path("AGENTS.md"),
    Path("docs/agent/CONTEXT.md"),
    Path("docs/agent/ARCHITECTURE.md"),
    Path("docs/agent/CODEMAP.md"),
    Path("docs/agent/RUNTIME.md"),
)
TEMPLATES = Path(__file__).resolve().parent / "templates"
DRAFT_MARKER = "<!-- agent-docs-kit:draft -->"
PLACEHOLDER = re.compile(r"\{\{[^}]+\}\}")
INLINE_LINK = re.compile(r"!?\[[^]\n]*\]\((<[^>\n]+>|[^\s)]+)(?:\s+\"[^\"\n]*\")?\)")
LIMITS = (
    "Comprobación estructural solamente. Revisar manualmente semántica, "
    "enlaces de referencia, sintaxis Markdown compleja y anclas."
)


def _safe_path(path: Path) -> Path:
    """Check each effective component, including those before '..'."""
    absolute = path.expanduser()
    if not absolute.is_absolute():
        absolute = Path.cwd() / absolute
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor = cursor.parent if part == ".." else cursor / part
        if cursor.is_symlink():
            raise ValueError(f"Enlace simbólico no permitido: {cursor}")
    return cursor


def _root(path: Path) -> Path:
    root = _safe_path(path)
    if not root.is_dir():
        raise ValueError(f"La raíz debe ser un directorio existente: {root}")
    return root


def _conflicts(root: Path) -> tuple[str, ...]:
    conflicts: list[str] = []
    for relative in DOCUMENTS:
        target = root / relative
        try:
            _safe_path(target)
        except ValueError as exc:
            conflicts.append(str(exc))
            continue
        if target.exists():
            conflicts.append(f"Destino ocupado: {relative}")
        else:
            for parent in target.parents:
                if parent == root:
                    break
                if parent.exists() and not parent.is_dir():
                    conflicts.append(f"El directorio padre no es directorio: {parent}")
                    break
    return tuple(conflicts)


def inspect(root: Path) -> tuple[str, ...]:
    """List exact destination conflicts, not semantic authorities."""
    return _conflicts(_root(root))


def _make_dirs(path: Path, created: list[Path]) -> None:
    if path.is_dir():
        return
    _make_dirs(path.parent, created)
    path.mkdir()
    created.append(path)


def _external_destination(source: Path, output: Path) -> Path:
    destination = _safe_path(output)
    # Filesystem identity also catches case aliases on case-insensitive volumes.
    inside_source = any(
        path.exists() and path.samefile(source)
        for path in (destination, *destination.parents)
    )
    contains_source = destination.exists() and any(
        path.samefile(destination) for path in (source, *source.parents)
    )
    if inside_source or contains_source:
        raise ValueError("La salida debe estar fuera de la raíz y no contenerla.")
    return destination


def prepare(root: Path, output: Path | None = None) -> None:
    """Create all drafts exclusively; CLI callers must authorize writing."""
    source = _root(root)
    destination = source if output is None else _external_destination(source, output)
    if destination.exists() and not destination.is_dir():
        raise ValueError(f"El destino no es un directorio: {destination}")
    conflicts = _conflicts(destination)
    if conflicts:
        raise FileExistsError("\n".join(conflicts))
    # Read every template before creating any destination entry.
    contents = tuple((TEMPLATES / path).read_bytes() for path in DOCUMENTS)
    created_files: list[Path] = []
    created_dirs: list[Path] = []
    try:
        for relative, data in zip(DOCUMENTS, contents, strict=True):
            target = destination / relative
            _safe_path(target)
            _make_dirs(target.parent, created_dirs)
            with target.open("xb") as stream:
                created_files.append(target)
                stream.write(data)
    except (OSError, ValueError):
        cleanup_errors: list[str] = []
        for path in reversed(created_files):
            try:
                path.unlink()
            except OSError as exc:
                cleanup_errors.append(f"{path}: {exc}")
        for path in reversed(created_dirs):
            try:
                path.rmdir()
            except OSError as exc:
                cleanup_errors.append(f"{path}: {exc}")
        if cleanup_errors:
            print("Limpieza incompleta: " + "; ".join(cleanup_errors), file=sys.stderr)
        raise


def _without_code(text: str) -> str:
    lines: list[str] = []
    fence: str | None = None
    paragraph = False
    for line in text.splitlines():
        expanded = line.expandtabs(4)
        if not expanded.strip():
            paragraph = False
        # CommonMark: an indented code block cannot interrupt a paragraph.
        # This also covers simple list-item continuations. Nested containers
        # remain outside this intentionally limited Markdown checker.
        if expanded.startswith("    ") and (fence is not None or not paragraph):
            continue
        marker = re.match(r" {0,3}(`{3,}|~{3,})(.*)$", expanded)
        if marker:
            run, suffix = marker.groups()
            if fence is None:
                fence = run
                paragraph = False
            elif (
                run[0] == fence[0]
                and len(run) >= len(fence)
                and not suffix.strip(" \t")
            ):
                fence = None
            continue
        if fence is None:
            lines.append(line)
            paragraph = bool(expanded.strip()) and not bool(re.match(
                r" {0,3}(?:#{1,6}(?:\s|$)|(?:[-*_]\s*){3,}$|=+\s*$)", expanded
            ))
    return re.sub(r"(`+)[^`\n]*?\1", "", "\n".join(lines))

def _link_findings(root: Path, document: Path, text: str) -> tuple[str, ...]:
    findings: list[str] = []
    for match in INLINE_LINK.finditer(_without_code(text)):
        raw = match.group(1).removeprefix("<").removesuffix(">")
        try:
            url = urlsplit(raw)
            if url.scheme or url.netloc or not url.path:
                continue
            decoded = unquote(url.path)
            if Path(decoded).is_absolute():
                findings.append(f"{document}: enlace local absoluto: {raw}")
                continue
            target = _safe_path((root / document).parent / decoded)
            if not target.is_relative_to(root):
                findings.append(f"{document}: enlace fuera de la raíz: {raw}")
            elif not target.is_file():
                findings.append(f"{document}: enlace local ausente o no regular: {raw}")
        except ValueError as exc:
            findings.append(f"{document}: enlace no comprobable {raw}: {exc}")
    return tuple(findings)


def check(root: Path) -> tuple[str, ...]:
    """Check supported structure, never execute target repository commands."""
    directory = _root(root)
    findings: list[str] = []
    for relative in DOCUMENTS:
        try:
            target = _safe_path(directory / relative)
            if not target.is_file():
                findings.append(f"{relative}: documento ausente o no regular")
                continue
            data = target.read_bytes()
            text = data.decode("utf-8")
        except ValueError as exc:
            findings.append(f"{relative}: no se puede leer: {exc}")
            continue
        if b"\r" in data:
            findings.append(f"{relative}: usar saltos de línea LF")
        if not text.strip():
            findings.append(f"{relative}: documento vacío")
        if DRAFT_MARKER in text or PLACEHOLDER.search(text):
            findings.append(f"{relative}: borrador pendiente")
        findings.extend(_link_findings(directory, relative, text))
    return tuple(findings)


def graph(
    root: Path, output: Path, excludes: Sequence[str] = (), *, timeout: float = 120.0,
) -> Path:
    """Run the tested code-only Graphify workflow into a new external folder."""
    source = _root(root)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("El timeout debe ser un número positivo y finito.")
    destination = _external_destination(source, output)
    if destination.exists():
        raise FileExistsError(f"La salida debe ser nueva: {destination}")
    executable = shutil.which("graphify")
    if executable is None:
        raise ValueError("Falta Graphify: instalar aparte con uv tool install graphifyy==0.9.26")
    executable = os.path.abspath(executable)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("GRAPHIFY_")}
    environment.update(GRAPHIFY_OUT="graphify-out", PYTHONDONTWRITEBYTECODE="1")
    try:
        version = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, check=False,
            env=environment, timeout=min(timeout, 10.0),
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Graphify --version excedió el tiempo límite.") from exc
    if version.returncode != 0 or version.stdout.strip() != "graphify 0.9.26":
        raise ValueError("Se requiere Graphify 0.9.26; no se ejecutó extracción.")
    command = [
        executable, "extract", str(source), "--code-only", "--no-cluster",
        "--max-workers", "1", "--out", str(destination),
    ]
    for pattern in excludes:
        command.extend(["--exclude", pattern])
    metadata: dict[str, object] = {
        "status": "running", "root": str(source), "version": version.stdout.strip(),
        "command": command, "timeout_seconds": timeout,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    destination.mkdir(parents=True)
    record = destination / "run.json"
    events = destination / "run.jsonl"
    with events.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps({**metadata, "event": "started"}, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    log = destination / "graphify.log"
    log.touch(exist_ok=False)
    try:
        try:
            result = subprocess.run(
                command, cwd=destination, env=environment,
                capture_output=True, text=True, check=False, timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            def partial(value: bytes | str | None) -> str:
                return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
            with log.open("a", encoding="utf-8") as stream:
                stream.write(partial(exc.stdout) + "\n" + partial(exc.stderr))
            raise ValueError(f"Graphify excedió el tiempo límite ({timeout:g} s).") from exc
        with log.open("a", encoding="utf-8") as stream:
            stream.write(result.stdout + "\n" + result.stderr)
        if result.returncode != 0:
            raise ValueError(f"Graphify falló ({result.returncode}); consultar {log}")
        artifact = _safe_path(destination / "graphify-out/graph.json")
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("nodes"), list)
            or not payload["nodes"]
            or not isinstance(payload.get("edges"), list)
        ):
            raise ValueError("Graphify no produjo un grafo no vacío con nodos y aristas.")
    except (OSError, ValueError) as exc:
        metadata["status"] = "failed"
        metadata["error"] = str(exc)
        metadata["finished_at"] = datetime.now(timezone.utc).isoformat()
        with log.open("a", encoding="utf-8") as stream:
            stream.write(f"\nError del kit: {exc}\n")
        _finish_graph_record(record, events, metadata)
        raise
    metadata["status"] = "complete"
    metadata["finished_at"] = datetime.now(timezone.utc).isoformat()
    _finish_graph_record(record, events, metadata)
    return artifact


def _finish_graph_record(record: Path, events: Path, metadata: dict[str, object]) -> None:
    """Keep run transitions, and create the compatibility summary just once."""
    with events.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({**metadata, "event": "finished"}, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    with record.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepara borradores y detecta cambios en evidencia; el agente investiga y revisa."
    )
    parser.add_argument("--version", action="version", version=f"agent-docs-kit {VERSION}")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("inspect", "Inspeccionar destinos sin escribir"),
        ("prepare", "Crear borradores con autorización explícita"),
        ("check", "Comprobar estructura sin certificar contenido"),
        ("graph", "Extraer grafo de código con Graphify externo, sin LLM"),
        ("snapshot", "Registrar una línea base de documentos y fuentes, sin aprobar contenido"),
        ("status", "Detectar cambios respecto de una línea base, sin modificar archivos"),
        ("append", "Agregar un evento de sesión sin reemplazar historia"),
        ("verify", "Verificar el historial JSONL sin modificarlo"),
        ("render", "Leer el historial como Markdown o crear una exportación nueva"),
        ("facts", "Contrastar hechos documentales con sus fuentes declaradas"),
        ("index-check", "Verificar blobs preparados en Git y conservación del historial"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("root", type=Path, help="Raíz existente del repositorio")
        if name in {"prepare", "graph", "snapshot"}:
            command.add_argument(
                "--output", type=Path, required=name in {"graph", "snapshot"}, help="Ruta de salida explícita"
            )
            command.add_argument("--apply", action="store_true", help="Autorizar escritura")
        if name in {"status", "index-check"}:
            command.add_argument("--knowledge-map", help="Opt-in root-relative knowledge map; checks declared review obligations")
        if name == "prepare":
            command.add_argument(
                "--authorities-reviewed", action="store_true",
                help="Confirmar revisión de instrucciones y autoridades en otras rutas",
            )
        elif name == "graph":
            command.add_argument("--exclude", action="append", default=[], help="Excluir patrón")
            command.add_argument("--timeout", type=float, default=120.0, help="Límite de extracción en segundos (120)")
        elif name == "snapshot":
            command.add_argument("--source", action="append", required=True, help="Fuente relativa a ROOT, archivo o carpeta (repetible)")
            command.add_argument("--document", action="append", required=True, help="Documento existente relativo a ROOT (repetible)")
            command.add_argument("--discover-root", action="append", default=None,
                                 help="Directorio relativo a ROOT para detectar rutas sin cobertura (repetible; requiere Git)")
        elif name == "status":
            command.add_argument("--snapshot", type=Path, required=True, help="JSON de línea base")
            command.add_argument("--json", action="store_true", help="Resultado y errores operativos como JSON")
        elif name in {"append", "verify", "render"}:
            command.add_argument("--history", default="docs/living/history", help="Directorio relativo a ROOT")
            command.add_argument("--session", required=name == "append", help="UUID de sesión")
            if name == "append":
                command.add_argument("--key", required=True, help="Clave estable para reintentos de la misma operación")
                command.add_argument("--kind", default="note", help="Tipo; session.closed cierra el segmento")
                content = command.add_mutually_exclusive_group(required=True)
                content.add_argument("--summary", help="Texto que se conserva dentro del evento")
                content.add_argument("--payload", help="Objeto JSON; alternativa a summary/file/command")
                command.add_argument("--file", action="append", default=[], help="Referencia informativa a archivo")
                command.add_argument("--command", dest="recorded_commands", action="append", default=[])
                command.add_argument("--source", default="cli", help="Origen declarado del evento")
                command.add_argument("--timeout", type=float, default=5.0, help="Espera máxima del lock")
                command.add_argument("--apply", action="store_true", help="Autorizar append")
            elif name == "render":
                command.add_argument("--output", type=Path, help="Exportación nueva; sin output imprime en stdout")
                command.add_argument("--apply", action="store_true", help="Autorizar creación de exportación")
            else:
                command.add_argument("--json", action="store_true")
        elif name == "facts":
            command.add_argument("--manifest", type=Path, required=True, help="JSON de hechos relativo a ROOT")
            command.add_argument("--json", action="store_true")
        elif name == "index-check":
            command.add_argument("--snapshot", required=True, help="Snapshot versionado, relativo a ROOT")
            command.add_argument("--history", default="docs/living/history", help="Historial relativo a ROOT")
            command.add_argument("--facts", help="Manifiesto de hechos preparado, relativo a ROOT")
            command.add_argument("--json", action="store_true")
    sys.dont_write_bytecode = True
    from knowledge_cli import add_parser
    add_parser(commands)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "knowledge" and args.knowledge_command == "accept" and (not args.apply or not args.confirm_reviewed):
        parser.error("knowledge accept requires --apply and --confirm-reviewed")
    if args.command == "knowledge" and args.knowledge_command == "propose" and not args.apply:
        parser.error("knowledge propose requires --apply")
    if args.command == "knowledge" and args.knowledge_command in ("check", "read") and bool(args.output) != bool(args.apply):
        parser.error("knowledge export requires both --output and --apply")
    if args.command in {"prepare", "graph", "snapshot", "append"} and not args.apply:
        parser.error(f"{args.command} requiere --apply para autorizar escritura")
    if args.command == "render" and args.output is not None and not args.apply:
        parser.error("render --output requiere --apply")
    if args.command == "append" and args.payload is not None and (args.file or args.recorded_commands):
        parser.error("--payload no admite --file ni --command")
    if args.command == "prepare" and args.output is None and not args.authorities_reviewed:
        parser.error("prepare en el repositorio requiere --authorities-reviewed")
    try:
        # All local imports must preserve the read-only commands' filesystem contract.
        sys.dont_write_bytecode = True
        if args.command == "knowledge":
            from knowledge_cli import run
            return run(args)
        if args.command == "append":
            from history import append_event
            from living_docs import _unique_object, _invalid_constant
            payload = (json.loads(args.payload, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
                       if args.payload is not None else
                       {"summary": args.summary, "files": args.file, "commands": args.recorded_commands})
            event = append_event(args.root, args.history, args.session, args.kind,
                                 payload, args.key, args.source, args.timeout)
            print(json.dumps(event, ensure_ascii=False))
            return 0
        if args.command == "verify":
            from history import verify_history
            report = verify_history(args.root, args.history, args.session)
            print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else
                  f"{report['state']}: {report['event_count']} eventos; revisión semántica no certificada.")
            return 0
        if args.command == "render":
            from history import render_history
            rendered = render_history(args.root, args.history, args.session)
            if args.output is None:
                print(rendered, end="" if rendered.endswith("\n") else "\n")
            else:
                target = _safe_path(args.output)
                # Parent must exist: export cannot silently build or replace a document tree.
                with target.open("x", encoding="utf-8") as stream:
                    stream.write(rendered)
                    stream.flush()
                    os.fsync(stream.fileno())
                print(str(target))
            return 0
        if args.command == "facts":
            from facts import check_facts
            report = check_facts(args.root, args.manifest)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1 if report["state"] == "review_required" else 0
        if args.command == "index-check":
            from git_gate import check_index
            report = check_index(args.root, args.snapshot, args.history, args.facts, knowledge_map=args.knowledge_map)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1 if report["state"] == "review_required" else 0
        if args.command == "snapshot":
            sys.dont_write_bytecode = True
            from living_docs import snapshot
            record = snapshot(args.root, args.source, args.document, args.output,
                              discover_roots=args.discover_root)
            print(json.dumps(record, ensure_ascii=False, indent=2))
            return 0
        if args.command == "status":
            sys.dont_write_bytecode = True
            from living_docs import status
            report = status(args.root, args.snapshot, knowledge_map=args.knowledge_map)
            if args.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                print(report["state"])
                print("Comparación de bytes dentro del alcance declarado; revisión semántica no certificada.")
                for group in ("sources", "documents"):
                    for change, paths in report[group].items():
                        if paths:
                            print(f"{group}.{change}: {', '.join(paths)}")
                if report.get("coverage_check"):
                    coverage = report["coverage_check"]
                    print(f"Cobertura ({coverage['view']}): {coverage['state']}")
                    for item in coverage["uncovered"]:
                        print(f"Sin cobertura: {item['path']!r} ({item['git']})")
                if report.get("scope_changes"):
                    print("Cambio de tipo o presencia de un alcance; revisar el resultado JSON.")
                if any(report.get("directory_changes", {}).values()):
                    print("Cambio de directorios observados; revisar el resultado JSON.")
            return 1 if report["state"] == "review_required" else 0
        if args.command == "graph":
            artifact = graph(args.root, args.output, args.exclude, timeout=args.timeout)
            print(f"Grafo de código: {artifact}")
            print("Evidencia derivada; revisar relaciones y redactar CODEMAP con el agente.")
            return 0
        if args.command == "prepare":
            prepare(args.root, args.output)
            print(f"Borradores creados en: {args.output or args.root}")
            print("Pendiente: autoría del agente y revisión semántica. Consultar AUTHORING.md.")
            return 0
        findings = inspect(args.root) if args.command == "inspect" else check(args.root)
        for finding in findings:
            print(finding)
        if args.command == "inspect":
            if not findings:
                print("Cinco destinos libres; esto no prueba ausencia de autoridades previas.")
            print("Revisar instrucciones y documentación canónica antes de autorizar escritura.")
        else:
            print(LIMITS)
        return 1 if findings else 0
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, ValueError, RecursionError) as exc:
        if getattr(args, "json", False) or args.command == "append":
            print(json.dumps({"state": "error", "error": str(exc), "semantic_review": "not_attested"}, ensure_ascii=False))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
