"""Bounded current context from hash-verified acceptance artifacts in one view."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

from doc_evidence import _required, digest, open_view, revalidate
from doc_links import canonical, ordered_sections, parse_links, path, section_spans
from doc_revisions import MAX_DOCUMENT, resolved_context

MAX_OUTPUT = 32 * 1024
MAX_SECTIONS = 8


def selection_metadata(reader, links, map_path, context, accepted, *, snapshot_path=None):
    """Verified internal resolver context; no origin substitution after adoption."""
    current_map = {"path": map_path, "sha256": digest(_required(reader, map_path, MAX_DOCUMENT))}
    records = {}
    for spec in links["documents"]:
        did = spec["id"]
        selection = context["selection"][did]
        record = {
            "document_id": did,
            "origin": spec["origin"],
            "relative_link_base": PurePosixPath(spec["origin"]).parent.as_posix(),
            "map": current_map,
            "selection": selection,
            "heads": context["heads"][did],
            "state": selection,
            "freshness": {"state": "not_evaluated", "reasons": [selection]},
            "claims": [{"section_id": s["id"], "claim_kind": s["claim_kind"]} for s in spec["sections"]],
        }
        if selection == "conflict":
            record["conflict_revisions"] = [
                {
                    "event_id": b["event"]["event_id"],
                    "revision": b["event"]["payload"]["revision"]["path"],
                    "proposal": b["event"]["payload"]["proposal"]["path"],
                    "review": b["event"]["payload"]["review"]["path"],
                }
                for b in accepted[did]["parent_bundles"]
            ]
        if selection == "accepted":
            bundle = accepted[did]
            event = bundle["event"]
            manifest = bundle["manifest"]
            record["lineage"] = {
                "event_id": event["event_id"],
                "parents": event["payload"]["parents"],
                "proposal_id": manifest["proposal_id"],
                "revision": event["payload"]["revision"]["path"],
                "proposal": event["payload"]["proposal"]["path"],
                "review": event["payload"]["review"]["path"],
            }
            if bundle["evidence"]["map"] != current_map:
                freshness = {
                    "state": "stale",
                    "reasons": ["map_changed"],
                    "view": reader.kind,
                    "origin_view": bundle["evidence"]["view"],
                }
            else:
                freshness = revalidate(
                    reader,
                    bundle["evidence"],
                    phase="selected",
                    resolved=context,
                    selected={
                        "document_id": did,
                        "path": event["payload"]["revision"]["path"],
                        "sha256": manifest["document"]["sha256"],
                    },
                    snapshot_path=snapshot_path,
                )
            record["freshness"] = freshness
            record["state"] = "current" if freshness["state"] == "matches_review" else "stale"
        records[did] = record
    return records


def _bounded(value):
    if len(canonical(value)) > MAX_OUTPUT:
        raise ValueError("Current output budget exceeded")
    return value


def read_current(root: Path, map_path: str, *, view="worktree", document_id=None, section_id=None):
    if document_id is not None and section_id is not None:
        raise ValueError("Choose a document or section")
    path(map_path)
    reader = open_view(root, view)
    links = parse_links(_required(reader, map_path, MAX_DOCUMENT))
    context, accepted = resolved_context(reader, links)
    metadata = selection_metadata(reader, links, map_path, context, accepted)
    documents = {d["id"]: d for d in links["documents"]}
    owners = {s["id"]: d["id"] for d in links["documents"] for s in d["sections"]}
    sections = {s["id"]: s for d in links["documents"] for s in d["sections"]}
    result = {
        "schema": "agent-docs-kit/current",
        "schema_version": 1,
        "view": view,
        "semantic_review": "not_attested",
    }
    unmapped = {
        did: section_spans(context["base_documents"][did], spec)["unmapped_headings"]
        for did, spec in documents.items()
        if context["selection"][did] == "unaccepted"
        or accepted[did]["evidence"]["map"]
        == {"path": map_path, "sha256": digest(_required(reader, map_path, MAX_DOCUMENT))}
    }
    # Fresh selected envelopes have exactly the current inventory; stale ones
    # retain provenance but do not claim current coverage.
    unknown = any(r["state"] != "current" for r in metadata.values())
    not_evaluated = sorted(
        {p for b in accepted.values() for p in b["evidence"]["inventory"]["not_evaluated"]}
    )
    partial = bool(not_evaluated or any(unmapped.values()))
    result["coverage"] = {
        "state": "not_evaluated" if unknown else ("partial" if partial else "declared_scope"),
        "not_evaluated": not_evaluated,
        "unmapped_sections": unmapped,
        "inventory_only": [dict(item) for item in links["inventory_only"]],
        "assurance_note": "Inventory-only entries have path tracking, not semantic review. "
        "Only declared sources are covered; prose correctness is not attested.",
    }
    if document_id is None and section_id is None:
        result.update(
            state="review_required" if unknown or partial else "current",
            documents=[metadata[d] for d in sorted(metadata)],
            exit_code=1 if unknown or partial else 0,
        )
    elif document_id is not None:
        if document_id not in documents:
            raise ValueError("Unknown document selector")
        record = metadata[document_id]
        result.update(record, exit_code=0 if record["state"] == "current" else 1)
        if record["state"] == "current":
            if len(documents[document_id]["sections"]) > MAX_SECTIONS:
                raise ValueError("Current section budget exceeded")
            result["body"] = accepted[document_id]["document"].decode("utf-8")
    else:
        if section_id not in sections:
            raise ValueError("Unknown section selector")
        closure = set()
        pending = [section_id]
        while pending:
            sid = pending.pop()
            if sid in closure:
                continue
            closure.add(sid)
            pending.extend(sections[sid]["depends_on"])
        if len(closure) > MAX_SECTIONS:
            raise ValueError("Current section budget exceeded")
        selected_docs = sorted({owners[sid] for sid in closure})
        records = [metadata[d] for d in selected_docs]
        bad = next((r for r in records if r["state"] != "current"), None)
        result.update(
            section_id=section_id,
            state=bad["state"] if bad else "current",
            selection=context["selection"][owners[section_id]],
            documents=records,
            exit_code=1 if bad else 0,
        )
        if bad is None:
            result["sections"] = []
            for sid in ordered_sections(links):
                if sid not in closure:
                    continue
                did = owners[sid]
                raw = accepted[did]["document"]
                start, end = section_spans(raw, documents[did])["spans"][sid]
                result["sections"].append(
                    {
                        "section_id": sid,
                        "document_id": did,
                        "claim_kind": sections[sid]["claim_kind"],
                        "body": raw[start:end].decode("utf-8"),
                    }
                )
    # Local freshness does not resolve unrelated or partial global coverage.
    # Inventory-only is a declared disposition, not semantic assurance.
    if unknown or partial:
        result["exit_code"] = 1
    reader.assert_unchanged()
    return _bounded(result)


def render_current(result, output_format="markdown"):
    if output_format not in ("markdown", "json"):
        raise ValueError("Unsupported current output format")
    if output_format == "json":
        rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    else:
        lines = [
            "# Current knowledge context",
            "",
            f"State: {result['state']}",
            "Semantic review: not_attested",
            "",
            f"Coverage: {result['coverage']['state']}",
            "",
        ]
        # Compact provenance and unresolved coverage stay visible in Markdown too.
        metadata = {
            key: result[key]
            for key in ("origin", "relative_link_base", "map", "lineage", "coverage")
            if key in result
        }
        if "documents" in result:
            metadata["documents"] = [
                {
                    key: record[key]
                    for key in ("document_id", "origin", "relative_link_base", "map", "lineage", "state")
                    if key in record
                }
                for record in result["documents"]
            ]
        lines.extend(
            [
                "Provenance and coverage: " + json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                "",
                "Relative links in preserved bodies use the document relative_link_base "
                "(relative to repository root), not the revision or export directory.",
                "",
            ]
        )
        if "body" in result:
            lines.extend(
                [
                    "Claims: " + ", ".join(c["section_id"] + "=" + c["claim_kind"] for c in result["claims"]),
                    "",
                    result["body"],
                ]
            )
        elif "sections" in result:
            for section in result["sections"]:
                lines.extend(
                    [f"Section: {section['section_id']} ({section['claim_kind']})", "", section["body"], ""]
                )
        else:
            for record in result.get("documents", [result]):
                lines.append(f"{record.get('document_id', 'context')}: {record['state']}")
            lines.extend(["", json.dumps(result, ensure_ascii=False, sort_keys=True)])
        rendered = "\n".join(lines) + "\n"
    if len(rendered.encode("utf-8")) > MAX_OUTPUT:
        raise ValueError("Current output budget exceeded")
    return rendered
