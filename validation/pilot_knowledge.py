#!/usr/bin/env python3
"""Standalone documentary pilot for an existing isolated diagram-library fixture.

Runs project commands only in a new controlled copy with existing dependencies.
Never installs dependencies, repairs tests, stages changes in the input, or calls
an LLM. The fresh-context utility study is a separate, parent-owned experiment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

EXCLUDED = {".git", ".venv", "__pycache__", "node_modules", "dist", ".next", ".wrangler"}
DOCUMENT = "docs/living/PROJECT.md"
MAP = "docs/living/maps/knowledge-pilot.json"
SNAPSHOT = "docs/living/snapshots/knowledge-pilot.json"
EXPECTED_FAILURES = [
    "server-renders the starter loading skeleton",
    "keeps the loading skeleton scoped and disposable",
]


def validate_locations(repo, kit, output):
    roots = tuple(Path(p).resolve() for p in (repo, kit, output))
    for p in roots[:2]:
        if not p.is_dir():
            raise ValueError("Input must be an existing directory")
    for i, left in enumerate(roots):
        for right in roots[i + 1 :]:
            if left.is_relative_to(right) or right.is_relative_to(left):
                raise ValueError("Pilot input, kit and output must be disjoint")
    if roots[-1].exists():
        raise ValueError("Output must be a new directory")
    return roots


def inventory(root):
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED)
        for d in dirs:
            if (Path(directory) / d).is_symlink():
                raise ValueError("Symlink source directory")
        for name in sorted(files):
            p = Path(directory) / name
            if name.endswith((".pyc", ".pyo")):
                continue
            if p.is_symlink() or not p.is_file():
                raise ValueError("Nonregular source")
            result[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return result


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")


def curate(root, sources):
    # Exact host headings and exact source paths; routing depends on both the
    # catalogue description and the known test contracts in its table.
    sections = [
        (
            "project.exists",
            "## Qué existe",
            ["app/page.tsx", "package.json", "vite.config.ts", "worker/index.ts"],
            [],
        ),
        ("project.change", "## Dónde comenzar un cambio", [], ["project.exists", "project.pending"]),
        (
            "project.pending",
            "## Pendientes conocidos, anteriores al kit",
            ["tests/rendered-html.test.mjs", "README.md"],
            [],
        ),
        ("project.scope", "## Alcance de esta adopción", ["docs/living/README.md"], []),
    ]
    body = (root / DOCUMENT).read_text(encoding="utf-8")
    watched = {p for _, _, paths, _ in sections for p in paths}
    for _, heading, _, _ in sections:
        if body.splitlines().count(heading) != 1:
            raise ValueError("Unexpected PROJECT heading: " + heading)

    def reason(p):
        if p.startswith("public/"):
            return "Published asset inventory only; this pilot does not attest individual diagram content."
        if p.startswith(("db/", "examples/")):
            return "Starter/example inventory only; existence is not evidence of use by the catalogue."
        if p.startswith("docs/"):
            return "Preserved prior adoption artifact; historical claims are not re-reviewed by this pilot."
        return (
            "Build, styling or auxiliary source inventory only; no section-level semantic claim is inferred."
        )

    return {
        "schema": "agent-docs-kit/links",
        "schema_version": 1,
        "project_id": str(uuid.uuid4()),
        "documents": [
            {
                "id": "project",
                "origin": DOCUMENT,
                "sections": [
                    {
                        "id": sid,
                        "selector": {"kind": "atx", "heading": heading},
                        "claim_kind": "observed",
                        "watches": paths,
                        "depends_on": dependencies,
                    }
                    for sid, heading, paths, dependencies in sections
                ],
            }
        ],
        "inventory_only": [{"path": p, "reason": reason(p)} for p in sorted(set(sources) - watched)],
    }


def native_failures(text):
    """Node chooses TAP or spec output according to its runtime configuration."""
    names = re.findall(r"^not ok \d+ - (.+)$", text, re.M)
    names += re.findall(r"^✖ (.+?) \([\d.]+ms\)$", text, re.M)
    return sorted(set(names))


def compare_native_tests(before, after):
    """Compare observed test outcomes; historical failures are advisory only."""

    def observed(record):
        code = record.get("exit")
        failures = record.get("failures")
        if (
            type(code) is not int
            or code not in (0, 1)
            or not isinstance(failures, list)
            or not all(isinstance(name, str) and name.strip() for name in failures)
            or (code == 0 and failures)
            or (code == 1 and not failures)
        ):
            raise RuntimeError("Native test runner did not produce coherent test outcomes")
        return set(failures)

    initial = observed(before)
    current = observed(after)
    introduced = sorted(current - initial)
    resolved = sorted(initial - current)
    return {
        "state": "regressed" if introduced else ("improved" if resolved else "unchanged"),
        "baseline_failures": sorted(initial),
        "after_failures": sorted(current),
        "introduced": introduced,
        "resolved_since_baseline": resolved,
        "resolved_historical": sorted(set(EXPECTED_FAILURES) - initial),
        "unexpected_baseline": sorted(initial - set(EXPECTED_FAILURES)),
    }


def pilot(repo, kit, output, *, timeout=1200):
    repo, kit, output = validate_locations(repo, kit, output)
    if not (kit / "agent_docs_kit.py").is_file():
        raise ValueError("Missing kit CLI")
    original = inventory(repo)
    if DOCUMENT not in original:
        raise ValueError("Expected the prepared diagram-library fixture")
    if not (repo / "node_modules").is_dir() or (repo / "node_modules").is_symlink():
        raise ValueError("Existing real dependency directory required; no install is performed")
    output.mkdir(parents=True)
    logs = output / "evidence"
    logs.mkdir()
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    environment.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_OPTIONAL_LOCKS="0",
        GIT_TERMINAL_PROMPT="0",
        PYTHONDONTWRITEBYTECODE="1",
        CI="1",
        WRANGLER_SEND_METRICS="false",
        DO_NOT_TRACK="1",
        NO_UPDATE_NOTIFIER="1",
    )

    def git(root, *args):
        p = subprocess.run(
            ["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(root), *args],
            env=environment,
            capture_output=True,
            timeout=30,
        )
        if p.returncode:
            raise RuntimeError(p.stderr.decode(errors="replace"))
        return p.stdout.decode().strip()

    original_head = git(repo, "rev-parse", "HEAD")
    index_path = Path(git(repo, "rev-parse", "--absolute-git-dir")) / "index"
    original_index = hashlib.sha256(index_path.read_bytes()).hexdigest()
    root = output / "repo"
    root.mkdir()
    for relative in original:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / relative, target)
    # On APFS clone file data; dependency links remain within the copied tree.
    # On other systems copy the existing tree, never link it to the input.
    if sys.platform == "darwin":
        subprocess.run(
            ["/bin/cp", "-cR", str(repo / "node_modules"), str(root / "node_modules")],
            check=True,
            capture_output=True,
            timeout=timeout,
        )
    else:
        shutil.copytree(repo / "node_modules", root / "node_modules", symlinks=True)
    for directory, dirs, files in os.walk(root / "node_modules", followlinks=False):
        for name in dirs + files:
            p = Path(directory) / name
            if p.is_symlink() and not p.resolve().is_relative_to(root / "node_modules"):
                raise ValueError("Dependency link escapes controlled copy")
    git(root, "init", "-q")
    git(root, "config", "user.name", "Synthetic Documentation Pilot")
    git(root, "config", "user.email", "pilot@example.invalid")
    git(root, "add", "-f", "--", *sorted(original))
    git(root, "commit", "-qm", "synthetic isolated pilot baseline")
    synthetic_head = git(root, "rev-parse", "HEAD")
    save(
        output / "source-baseline.json",
        {
            "source_sha256": original,
            "input_head": original_head,
            "input_index_sha256": original_index,
            "synthetic_baseline_head": synthetic_head,
        },
    )
    native = []
    commands = []

    def native_run(phase):
        for label, args in [
            ("lint", ["npm", "run", "lint"]),
            ("build", ["npm", "run", "build"]),
            ("test", ["npm", "test"]),
        ]:
            name = phase + "-" + label
            start = time.monotonic()
            with (logs / (name + ".log")).open("x") as f:
                try:
                    p = subprocess.run(
                        args, cwd=root, env=environment, stdout=f, stderr=subprocess.STDOUT, timeout=timeout
                    )
                    code = p.returncode
                except subprocess.TimeoutExpired:
                    code = 124
            text = (logs / (name + ".log")).read_text(errors="replace")
            failures = native_failures(text)
            native.append(
                {
                    "phase": phase,
                    "command": args,
                    "exit": code,
                    "seconds": round(time.monotonic() - start, 3),
                    "failures": failures,
                    "log": "evidence/" + name + ".log",
                }
            )
            save(logs / (name + "-result.json"), native[-1])
            if label == "test":
                baseline = next(
                    item
                    for item in native
                    if item["phase"] == "before" and item["command"] == ["npm", "test"]
                )
                comparison = compare_native_tests(baseline, native[-1])
                save(logs / (name + "-comparison.json"), comparison)
                if comparison["state"] == "regressed":
                    raise RuntimeError(
                        "Native test regressions relative to observed baseline; inspect " + name + ".log"
                    )
            elif code != 0:
                raise RuntimeError("Native check failed: " + name)

    def cli(label, *args, expected=0):
        p = subprocess.run(
            [sys.executable, "-B", str(kit / "agent_docs_kit.py"), *args],
            cwd=root,
            env=environment,
            capture_output=True,
            timeout=timeout,
        )
        (logs / (label + ".stdout")).write_bytes(p.stdout)
        (logs / (label + ".stderr")).write_bytes(p.stderr)
        commands.append(
            {
                "label": label,
                "args": list(args),
                "exit": p.returncode,
                "expected": expected,
                "stdout": "evidence/" + label + ".stdout",
                "stderr": "evidence/" + label + ".stderr",
            }
        )
        save(logs / (label + "-command.json"), commands[-1])
        if p.returncode != expected:
            raise RuntimeError("Unexpected CLI exit: " + label + "; see saved stdout/stderr")
        return json.loads(p.stdout) if "--json" in args or args[0] == "snapshot" else p.stdout.decode()

    native_run("before")
    # A bounded inventory scope: generated outputs and unrelated historical
    # reports are excluded. Coverage is explicitly partial, never whole-repo.
    sources = sorted(
        p for p in original if not p.startswith("docs/") and not p.startswith(".") and p != DOCUMENT
    )
    sources.append("docs/living/README.md")
    links = curate(root, sources)
    save(root / MAP, links)
    scopes = ["app", "db", "examples", "public", "tests", "worker"]
    args = ["snapshot", str(root), "--document", DOCUMENT, "--output", str(root / SNAPSHOT), "--apply"]
    for p in sources:
        args += ["--source", p]
    for p in scopes:
        args += ["--discover-root", p]
    cli("baseline", *args)
    body = (root / DOCUMENT).read_bytes()
    retained = {DOCUMENT: hashlib.sha256(body).hexdigest()}

    def preserve():
        for p in root.rglob("*"):
            relative = p.relative_to(root).as_posix()
            if p.is_file() and relative.startswith(
                (
                    "docs/living/revisions/",
                    "docs/living/reviews/",
                    "docs/living/history/",
                    "docs/living/snapshots/",
                    "docs/living/maps/",
                )
            ):
                retained.setdefault(relative, hashlib.sha256(p.read_bytes()).hexdigest())
        if not all(hashlib.sha256((root / p).read_bytes()).hexdigest() == h for p, h in retained.items()):
            raise ValueError("Preserved artifact changed")

    def check(label, snapshot=SNAPSHOT, expected=1):
        return cli(
            label,
            "knowledge",
            "check",
            str(root),
            "--map",
            MAP,
            "--snapshot",
            snapshot,
            "--json",
            expected=expected,
        )

    def proposal(label, body, mode, reviewed, report):
        request_id = str(uuid.uuid4())
        candidate = "pilot-" + label + ".md"
        request = "pilot-" + label + ".json"
        (root / candidate).write_bytes(body)
        save(
            root / request,
            {
                "schema": "agent-docs-kit/proposal-request",
                "schema_version": 1,
                "request_id": request_id,
                "project_id": links["project_id"],
                "document_id": "project",
                "mode": mode,
                "candidate_path": candidate,
                "expected_parents": report["evidence"]["heads"]["project"],
                "reviewed_sections": sorted(reviewed),
                "reason": "Agent reviewed the complete authorized sections against the isolated sources.",
                "evidence": report["evidence"],
            },
        )
        bundle = "docs/living/revisions/" + request_id
        manifest = cli(
            label + "-propose",
            "knowledge",
            "propose",
            str(root),
            "--request",
            request,
            "--output",
            bundle,
            "--apply",
            "--json",
        )
        review = "pilot-" + label + "-review.json"
        save(
            root / review,
            {
                "schema": "agent-docs-kit/review",
                "schema_version": 1,
                "proposal_id": manifest["proposal_id"],
                "decision": "accept",
                "reviewer": {"kind": "agent", "id": "documentation-pilot-agent"},
                "reason": "Checked source changes, complete authorized spans and unchanged outside bytes.",
                "review_reference": "pilot://" + label,
                "constraint_authorizations": [],
            },
        )
        preserve()
        return bundle, review

    def accept(label, p, expected=0):
        result = cli(
            label,
            "knowledge",
            "accept",
            str(root),
            "--proposal",
            p[0],
            "--review",
            p[1],
            "--session",
            str(uuid.uuid4()),
            "--key",
            label,
            "--confirm-reviewed",
            "--apply",
            "--json",
            expected=expected,
        )
        preserve()
        return result

    initial = check("bootstrap-impact")
    all_sections = [s["id"] for s in links["documents"][0]["sections"]]
    accept("bootstrap-accept", proposal("bootstrap", body, "bootstrap", all_sections, initial))
    cli("bootstrap-read", "knowledge", "read", str(root), "--map", MAP, "--document", "project", "--json")
    page = root / "app/page.tsx"
    text = page.read_text()
    old = "POC-PRIME-FALP-001 · Guía interactiva y diagramas visuales"
    if text.count(old) != 1:
        raise ValueError("Unexpected catalogue fixture")
    page.write_text(text.replace(old, old + " · piloto documental"))
    catalog = check("catalog-impact")
    expected_catalog = ["project.exists", "project.change"]
    if set(catalog["review_queue"]) != set(expected_catalog):
        raise ValueError("Unexpected catalogue impact")
    updated = body.replace(
        "una colección de diagramas,".encode(),
        "una colección de diagramas (con el primer título marcado «piloto documental» en esta copia),".encode(),
    )
    updated = updated.replace(
        "| Categorías, búsqueda o selector |".encode(),
        "| Categorías, búsqueda o selector (incluido el título de ensayo) |".encode(),
    )
    stale = proposal("catalog-stale", updated, "revise", expected_catalog, catalog)
    page.write_text(
        page.read_text().replace(
            "Topología, trazas de secuencia,", "Piloto documental: topología, trazas de secuencia,"
        )
    )
    accept("stale-rejected", stale, expected=1)
    updated = updated.replace(
        b"en esta copia),", "y su descripción identificada como piloto en esta copia),".encode()
    )
    fresh = check("catalog-fresh-impact")
    accept("catalog-accept", proposal("catalog-fresh", updated, "revise", expected_catalog, fresh))
    cli("catalog-read", "knowledge", "read", str(root), "--map", MAP, "--document", "project", "--json")
    tests = root / "tests/rendered-html.test.mjs"
    text = tests.read_text()
    marker = "  assert.match(html, developmentPreviewMeta);"
    if text.count(marker) != 1:
        raise ValueError("Unexpected native test fixture")
    tests.write_text(text.replace(marker, "  assert.match(html, /Biblioteca de diagramas/);\n" + marker))
    test_impact = check("test-impact")
    expected_test = ["project.pending", "project.change"]
    if set(test_impact["review_queue"]) != set(expected_test):
        raise ValueError("Unexpected test-source impact")
    refresh = "docs/living/snapshots/knowledge-pilot-refresh.json"
    refreshed_args = list(args)
    refreshed_args[refreshed_args.index("--output") + 1] = str(root / refresh)
    cli("snapshot-refresh", *refreshed_args)
    debt = check("refresh-debt", snapshot=refresh)
    if set(debt["review_queue"]) != set(expected_test):
        raise ValueError("Snapshot cleared review debt")
    updated = updated.replace(
        b"para el loading skeleton.",
        "para el loading skeleton; esta copia añade además una aserción del rótulo «Biblioteca de diagramas».".encode(),
    )
    updated = updated.replace(
        b"actualmente conserva expectativas del starter",
        "conserva expectativas del starter y añade una aserción del catálogo en la copia".encode(),
    )
    accept("test-accept", proposal("test-fresh", updated, "revise", expected_test, test_impact))
    current = cli(
        "current-json", "knowledge", "read", str(root), "--map", MAP, "--document", "project", "--json"
    )
    cli("current-markdown", "knowledge", "read", str(root), "--map", MAP, "--document", "project")
    if current.get("body") != updated.decode():
        raise ValueError("New-process read differs from reviewed candidate")
    (output / "knowledge-current.json").write_bytes((logs / "current-json.stdout").read_bytes())
    (output / "knowledge-current.md").write_bytes((logs / "current-markdown.stdout").read_bytes())
    preserve()
    native_run("after")
    unchanged = (
        inventory(repo) == original
        and git(repo, "rev-parse", "HEAD") == original_head
        and hashlib.sha256(index_path.read_bytes()).hexdigest() == original_index
    )
    if not unchanged:
        raise ValueError("Input fixture changed")
    report = {
        "state": "passed",
        "repo": str(root),
        "input_unchanged": unchanged,
        "original_head": original_head,
        "native_test_comparison": compare_native_tests(
            next(item for item in native if item["phase"] == "before" and item["command"] == ["npm", "test"]),
            next(item for item in native if item["phase"] == "after" and item["command"] == ["npm", "test"]),
        ),
        "native": native,
        "commands": commands,
        "catalog_queue": catalog["review_queue"],
        "test_queue": test_impact["review_queue"],
        "snapshot_refresh_queue": debt["review_queue"],
        "stale_proposal_rejected": True,
        "reviewer": {"kind": "agent", "id": "documentation-pilot-agent"},
        "semantic_review": "not_attested",
        "coverage": {
            "state": "partial",
            "reason": "Exact declared source inventory; inventory-only files are not semantically reviewed. Historical artifacts and generated outputs excluded.",
            "source_count": len(sources),
            "inventory_only": links["inventory_only"],
        },
        "retained_sha256": retained,
        "source_baseline": "source-baseline.json",
        "synthetic_baseline_head": synthetic_head,
        "intervention_json": "knowledge-current.json",
        "intervention_markdown": "knowledge-current.md",
        "context_evaluation": "NOT_RUN; parent owns the frozen fresh-context trial",
        "distribution": "NOT_RUN; parent packages after independent review",
    }
    save(output / "pilot.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", type=Path, required=True, help="Existing prepared isolated diagram-library fixture"
    )
    parser.add_argument("--kit", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True, help="New disjoint controlled output directory")
    parser.add_argument("--timeout", type=int, default=1200)
    a = parser.parse_args()
    if a.timeout <= 0:
        parser.error("timeout must be positive")
    try:
        result = pilot(a.repo, a.kit, a.output, timeout=a.timeout)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    print(
        json.dumps(
            {
                "state": result["state"],
                "report": str(a.output / "pilot.json"),
                "intervention": str(a.output / result["intervention_json"]),
            }
        )
    )
