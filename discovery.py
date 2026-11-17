"""Git-backed coverage discovery; never infers documentation correctness."""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import living_docs as living


def validate_policy(value: object) -> dict[str, Any]:
    policy = living._object(value, {"mode", "roots"}, "discovery")
    if policy["mode"] != "git" or not isinstance(policy["roots"], list) or not policy["roots"]:
        raise ValueError("Discovery requiere mode=git y roots no vacíos.")
    roots = [living._relative(p, canonical=True) for p in policy["roots"]]
    if roots != sorted(set(roots)):
        raise ValueError("Discovery roots deben ser únicos y ordenados.")
    for i, root in enumerate(roots):
        if any(living._within(root, p) or living._within(p, root) for p in roots[:i]):
            raise ValueError("Discovery roots no deben solaparse.")
    return policy


def make_policy(root: Path, values: Sequence[str]) -> dict[str, Any]:
    policy = validate_policy({"mode": "git", "roots": living._selected(values)})
    validate_roots(root, policy)
    # Reuse the read-only Git runner; no second subprocess implementation.
    from git_gate import _Git
    _Git(root)
    return policy


def validate_roots(root: Path, policy: dict[str, Any]) -> None:
    for relative in policy["roots"]:
        if living._kind(root / relative, missing_ok=True) != "directory":
            raise ValueError(f"Discovery root debe ser un directorio existente: {relative}")
        cursor = root
        for part in Path(relative).parts:
            # Git pathspecs use exact spelling; APFS may accept a case alias.
            # Refuse a root that exists only through filesystem case folding.
            if part not in {p.name for p in cursor.iterdir()}:
                raise ValueError(f"Discovery root requiere la grafía exacta del directorio: {relative}")
            cursor = cursor / part


def outside_discovery(output: Path, root: Path, policy: dict[str, Any]) -> None:
    living._outside_observed(output, root,
        [{"path": p, "kind": "directory"} for p in policy["roots"]], [])


def eligible(path: str, policy: dict[str, Any]) -> bool:
    return (not any(part in living.EXCLUSIONS for part in Path(path).parts)
            and any(living._within(path, scope) for scope in policy["roots"]))


def worktree_inventory(root: Path, policy: dict[str, Any]) -> tuple[bytes, str | None, dict[str, str]]:
    """Names and lstat only outside the selected sources; never reads new contents.

    The caller takes an inventory before and after fingerprinting selected files.
    This detects many races, not a coherent or adversary-proof filesystem snapshot.
    """
    from git_gate import _Git, _read_index, _decode, _index_path
    validate_roots(root, policy)
    git = _Git(root)
    index, entries = _read_index(git)
    head = git.head()
    candidates = {p: "tracked" for p in entries if eligible(p, policy)}
    raw = git.run("ls-files", "--others", "--exclude-standard", "-z", "--", *policy["roots"])
    if raw and not raw.endswith(b"\x00"):
        raise ValueError("Inventario Git incompleto: falta delimitador NUL.")
    for record in raw.split(b"\x00"):
        if not record:
            continue
        path = _index_path(_decode(record, "La ruta descubierta"))
        if eligible(path, policy):
            candidates.setdefault(path, "untracked")
    for path, state in candidates.items():
        kind = living._kind(root / path, missing_ok=True)
        if kind not in {"file", "missing"} or (kind == "missing" and state == "untracked"):
            raise ValueError(f"La ruta descubierta no es un archivo regular estable: {path}")
    if _read_index(git)[0] != index or git.head() != head:
        raise ValueError("El índice o HEAD cambió durante el descubrimiento; reintenta.")
    return index, head, candidates


def report(policy: dict[str, Any], view: str, candidates: dict[str, str],
           covered: set[str]) -> dict[str, Any]:
    uncovered = [{"path": path, "git": candidates[path]}
                 for path in sorted(candidates.keys() - covered)]
    return {"mode": "git", "view": view, "roots": policy["roots"],
            "state": "uncovered" if uncovered else "complete",
            "eligible_count": len(candidates),
            "covered_count": len(candidates.keys() & covered),
            "uncovered": uncovered,
            "note": "Cobertura de rutas en roots declarados, no aprobación semántica. "
                    "Worktree: tracked y untracked no ignorados; índice: sólo entradas preparadas. "
                    "Se conservan las exclusiones fijas del kit."}
