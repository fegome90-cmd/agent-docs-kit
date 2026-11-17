"""Opt-in knowledge CLI; review needs and operational errors stay distinct."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def add_parser(subparsers: Any) -> None:
    namespace = subparsers.add_parser("knowledge", help="Check declared documentary review obligations")
    commands = namespace.add_subparsers(dest="knowledge_command", required=True)
    check = commands.add_parser("check", help="Analyze one Git view without approving prose")
    check.add_argument("root", type=Path, help="Existing Git worktree root")
    check.add_argument("--map", required=True, help="Canonical links JSON path relative to ROOT")
    check.add_argument("--snapshot", required=True, help="Canonical snapshot path relative to ROOT")
    check.add_argument("--view", choices=("worktree", "index"), default="worktree")
    check.add_argument(
        "--history", default="docs/living/history", help="Reserved history path relative to ROOT"
    )
    check.add_argument("--json", action="store_true", help="Structured report and operational errors")
    check.add_argument("--output", help="Exclusive new report path relative to ROOT; parent must exist")
    check.add_argument("--apply", action="store_true", help="Authorize exclusive report export")

    propose = commands.add_parser("propose", help="Publish an exclusive reviewed-input proposal bundle")
    propose.add_argument("root", type=Path)
    propose.add_argument("--request", required=True, help="Worktree request JSON relative to ROOT")
    propose.add_argument("--output", required=True, help="Exclusive new bundle directory relative to ROOT")
    propose.add_argument("--apply", action="store_true", help="Authorize exclusive bundle publication")
    propose.add_argument("--json", action="store_true")

    accept = commands.add_parser("accept", help="Record an explicit declared review under live history locks")
    accept.add_argument("root", type=Path)
    accept.add_argument("--proposal", required=True, help="Safe worktree proposal bundle relative to ROOT")
    accept.add_argument("--review", required=True, help="Explicit worktree review JSON relative to ROOT")
    accept.add_argument("--session", required=True, help="Canonical session UUID")
    accept.add_argument("--key", required=True, help="Stable idempotency key")
    accept.add_argument("--confirm-reviewed", action="store_true")
    accept.add_argument("--apply", action="store_true")
    accept.add_argument("--json", action="store_true")

    read = commands.add_parser("read", help="Read bounded uniquely accepted context from one Git view")
    read.add_argument("root", type=Path)
    read.add_argument("--map", required=True)
    read.add_argument("--view", choices=("worktree", "index"), default="worktree")
    selector = read.add_mutually_exclusive_group()
    selector.add_argument("--document")
    selector.add_argument("--section")
    read.add_argument("--json", action="store_true")
    read.add_argument("--output", help="Exclusive new export relative to ROOT; parent must exist")
    read.add_argument("--apply", action="store_true")


def run(args: argparse.Namespace) -> int:
    from doc_revisions import ReviewRequired

    try:
        return _run(args)
    except ReviewRequired as exc:
        # Never downgrade generic ValueError/OSError based on message text.
        print(
            json.dumps(
                {
                    "state": "review_required",
                    "reason_code": exc.reason_code,
                    "message": str(exc),
                    "reasons": list(exc.reasons),
                    "semantic_review": "not_attested",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1


def _run(args: argparse.Namespace) -> int:
    if args.knowledge_command == "read":
        from doc_current import read_current, render_current

        result = read_current(
            args.root, args.map, view=args.view, document_id=args.document, section_id=args.section
        )
        rendered = render_current(result, "json" if args.json else "markdown")
        if args.output is not None:
            import living_docs as living
            from doc_links import path
            from doc_revisions import write_new

            write_new(
                living._safe_path(living._root(args.root) / path(args.output)), rendered.encode("utf-8")
            )
        print(rendered, end="")
        return result["exit_code"]
    if args.knowledge_command == "accept":
        from doc_revisions import accept_proposal

        result = accept_proposal(
            args.root,
            args.proposal,
            args.review,
            args.session,
            args.key,
            confirm_reviewed=args.confirm_reviewed,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result["exit_code"]
    if args.knowledge_command == "propose":
        from doc_revisions import create_proposal

        print(json.dumps(create_proposal(args.root, args.request, args.output), ensure_ascii=False, indent=2))
        return 0
    if bool(args.output) != bool(args.apply):
        # argparse usage errors remain distinct from operational failures.
        raise ValueError("Export requires both --output and --apply")
    from doc_impact import check_knowledge

    report = check_knowledge(args.root, args.map, args.snapshot, view=args.view, history_path=args.history)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        import living_docs as living
        from doc_links import path

        target = living._safe_path(living._root(args.root) / path(args.output))
        with target.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
    print(rendered, end="")
    return 1 if report["state"] == "review_required" else 0
