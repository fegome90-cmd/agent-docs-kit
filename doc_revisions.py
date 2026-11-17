"""Exclusive proposal bundles; declared review is never semantic attestation."""

from __future__ import annotations

import math
import os
from contextlib import contextmanager
from pathlib import Path

import living_docs as living
from doc_evidence import (
    MAX_EVIDENCE,
    _hash,
    _required,
    _sorted,
    _uuid,
    digest,
    evidence_id,
    open_view,
    revalidate,
)
from doc_links import canonical, identifier, load_json, obj, parse_links, path, section_spans

HISTORY = "docs/living/history"
MAX_DOCUMENT = 1024 * 1024
MAX_BUNDLE = 12 * 1024 * 1024
REQUEST_KEYS = {
    "schema",
    "schema_version",
    "request_id",
    "project_id",
    "document_id",
    "mode",
    "candidate_path",
    "expected_parents",
    "reviewed_sections",
    "reason",
    "evidence",
}
MANIFEST_KEYS = REQUEST_KEYS - {"candidate_path"} | {"document", "proposal_id"}


class ReviewRequired(ValueError):
    """A valid operation needs a new review, not schema or integrity repair.

    ValueError ancestry preserves module-level rejection compatibility. Only
    knowledge CLI operations map this precise type to exit 1; other failures
    retain their operational classification.
    """

    def __init__(self, reason_code: str, message: str, reasons=()):
        super().__init__(message)
        self.reason_code = reason_code
        self.reasons = tuple(reasons)


def schema(value, name, keys):
    obj(value, keys)
    if (
        value["schema"] != "agent-docs-kit/" + name
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
    ):
        raise ValueError("Unsupported " + name + " schema")
    return value


def text(value, *, nonempty=True):
    if not isinstance(value, str) or len(value.encode("utf-8")) > 65536 or (nonempty and not value.strip()):
        raise ValueError("Invalid declared text")
    return value


def descriptor(relative, raw):
    return {"path": relative, "sha256": digest(raw), "size": len(raw)}


def validate_descriptor(value):
    obj(value, {"path", "sha256", "size"})
    path(value["path"])
    _hash(value["sha256"])
    if type(value["size"]) is not int or value["size"] < 0:
        raise ValueError("Invalid artifact size")
    return value


def verified(view, ref, maximum):
    validate_descriptor(ref)
    raw = _required(view, ref["path"], maximum)
    if len(raw) != ref["size"] or digest(raw) != ref["sha256"]:
        raise ValueError("Artifact integrity mismatch")
    return raw


def write_new(target, raw):
    """Exclusive write. Any failed/unknown write preserves all existing bytes."""
    target = living._safe_path(target)
    with target.open("xb") as stream:
        if stream.write(raw) != len(raw):
            raise OSError("Incomplete artifact write")
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def knowledge_lock(root, timeout=5.0):
    import history

    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout < 0:
        raise ValueError("Invalid lock timeout")
    root, directory, _ = history._location(root, HISTORY)
    try:
        import portalocker
    except ImportError as exc:
        raise OSError("knowledge publishers require portalocker==4.4.2") from exc
    history._mkdirs(root, directory)
    target = directory / ".knowledge.lock"

    def opener(filename, flags):
        return history._open_file(Path(filename), flags | os.O_APPEND, create=True)

    try:
        with portalocker.Lock(
            str(target),
            mode="ab",
            timeout=timeout,
            check_interval=0.05,
            opener=opener,
            raise_on_release_error=True,
        ) as stream:
            history._check_handle(target, stream.fileno())
            yield
            history._check_handle(target, stream.fileno())
    except portalocker.exceptions.LockException as exc:
        raise OSError("Knowledge lock unavailable") from exc


def proposal_id(manifest, envelope):
    projection = {k: v for k, v in manifest.items() if k != "proposal_id"}
    # Replace the WHOLE date-variable descriptor, including its size.
    projection["evidence"] = envelope["evidence_id"]
    return digest(b"agent-docs-kit/proposal/v1\0" + canonical(projection))


def common(value):
    _uuid(value["request_id"])
    _uuid(value["project_id"])
    identifier(value["document_id"])
    if value["mode"] not in ("bootstrap", "revise", "no_impact", "resolve"):
        raise ValueError("Unknown proposal mode")
    _sorted(value["expected_parents"], _uuid)
    _sorted(value["reviewed_sections"], identifier)
    text(value["reason"], nonempty=value["mode"] in ("no_impact", "resolve"))


def load_proposal(view, bundle_path):
    bundle_path = path(bundle_path)
    raw = _required(view, bundle_path + "/proposal.json", MAX_DOCUMENT)
    manifest = schema(load_json(raw, MAX_DOCUMENT), "proposal", MANIFEST_KEYS)
    common(manifest)
    _hash(manifest["proposal_id"])
    for key, name in (("document", "document.md"), ("evidence", "evidence.json")):
        validate_descriptor(manifest[key])
        if manifest[key]["path"] != name:
            raise ValueError("Unexpected bundle member")

    def member(key, maximum):
        return verified(view, dict(manifest[key], path=bundle_path + "/" + manifest[key]["path"]), maximum)

    document = member("document", MAX_DOCUMENT)
    evidence_raw = member("evidence", MAX_EVIDENCE)
    if len(raw) + len(document) + len(evidence_raw) > MAX_BUNDLE:
        raise ValueError("Bundle size limit exceeded")
    envelope = load_json(evidence_raw, MAX_EVIDENCE)
    if evidence_id(envelope) != envelope["evidence_id"]:
        raise ValueError("Evidence identity mismatch")
    if envelope["project_id"] != manifest["project_id"] or manifest["expected_parents"] != envelope[
        "heads"
    ].get(manifest["document_id"]):
        raise ValueError("Proposal evidence binding mismatch")
    if proposal_id(manifest, envelope) != manifest["proposal_id"]:
        raise ValueError("Proposal identity mismatch")
    return {
        "manifest": manifest,
        "document": document,
        "evidence": envelope,
        "manifest_bytes": raw,
        "bundle_path": bundle_path,
    }


def validate_edit(base, candidate, spec, reviewed):
    """Compare every immutable gap and selected heading; only reviewed spans vary."""
    old = section_spans(base, spec)["spans"]
    new = section_spans(candidate, spec)["spans"]
    if set(reviewed) - old.keys():
        raise ValueError("Unknown reviewed section")
    old_order = sorted(old, key=lambda sid: old[sid][0])
    new_order = sorted(new, key=lambda sid: new[sid][0])
    if old_order != new_order:
        raise ValueError("scope_violation: section order")
    a = b = 0
    for sid in old_order:
        start, end = old[sid]
        nstart, nend = new[sid]
        if base[a:start] != candidate[b:nstart]:
            raise ValueError("scope_violation: unmanaged gap")
        if sid not in reviewed and base[start:end] != candidate[nstart:nend]:
            raise ValueError("scope_violation: unreviewed section")
        a, b = end, nend
    if base[a:] != candidate[b:]:
        raise ValueError("scope_violation: trailing gap")


def validate_candidate(reader, request, candidate, context, accepted):
    envelope = request["evidence"]
    did = request["document_id"]
    links = parse_links(_required(reader, envelope["map"]["path"], MAX_DOCUMENT))
    if links["project_id"] != request["project_id"]:
        raise ValueError("Wrong project")
    spec = next((d for d in links["documents"] if d["id"] == did), None)
    if spec is None:
        raise ValueError("Unknown document")
    if request["expected_parents"] != context["heads"][did]:
        raise ReviewRequired("parent_selection_changed", "Parent heads changed")
    result = revalidate(reader, envelope, phase="proposal", resolved=context)
    if result["state"] != "matches_review":
        raise ReviewRequired(
            "stale_evidence", "Proposal evidence expired: " + ",".join(result["reasons"]), result["reasons"]
        )
    base = context["base_documents"][did]
    mode = request["mode"]
    reviewed = request["reviewed_sections"]
    if mode == "bootstrap" and (context["heads"][did] or candidate != base):
        raise ValueError("Bootstrap must adopt exact unaccepted origin")
    if mode == "no_impact" and candidate != base:
        raise ValueError("No-impact must preserve body")
    if mode == "resolve" and context["selection"][did] != "conflict":
        raise ValueError("Resolve requires conflict")
    if context["selection"][did] == "conflict" and mode != "resolve":
        raise ReviewRequired("revision_conflict", "Conflict requires resolve")
    parents = accepted.get(did)
    parent_bundles = [] if parents is None else parents.get("parent_bundles", [parents])
    old_specs = []
    transition = False
    for parent in parent_bundles:
        old_ref = parent["evidence"]["map"]
        old_raw = _required(reader, old_ref["path"], MAX_DOCUMENT)
        if digest(old_raw) != old_ref["sha256"]:
            raise ValueError("Old map integrity mismatch")
        old_map = parse_links(old_raw)
        old_spec = next(d for d in old_map["documents"] if d["id"] == did)
        old_specs.append((old_spec, parent["document"]))
        transition |= old_ref != envelope["map"]
    all_sections = {s["id"] for s in spec["sections"]}
    if transition:
        union = all_sections | {s["id"] for old, _ in old_specs for s in old["sections"]}
        if mode not in ("revise", "resolve") or not request["reason"].strip() or set(reviewed) != union:
            raise ValueError("Map transition requires explicit full union review")
        section_spans(candidate, spec)
    else:
        if mode == "resolve":
            if set(reviewed) != all_sections or not request["reason"].strip():
                raise ValueError("Resolve requires full document review")
            # No conflict head may smuggle unmanaged gaps via the comparison anchor.
            for old, body in old_specs:
                validate_edit(body, base, old, sorted(all_sections))
        # Verify raw edit scope first, then the documentary dependency closure.
        # Source hashes alone cannot see prose-only changes in a supporting section.
        validate_edit(base, candidate, spec, reviewed)
        old_spans = section_spans(base, spec)["spans"]
        new_spans = section_spans(candidate, spec)["spans"]
        affected = {
            sid
            for sid, (start, end) in old_spans.items()
            if base[start:end] != candidate[slice(*new_spans[sid])]
        }
        from doc_links import ordered_sections

        sections = {
            section["id"]: section for document in links["documents"] for section in document["sections"]
        }
        for sid in ordered_sections(links):
            if affected.intersection(sections[sid]["depends_on"]):
                affected.add(sid)
        missing = sorted((affected & all_sections) - set(reviewed))
        if missing:
            raise ReviewRequired(
                "dependent_review_required",
                "Dependent section review required: " + ",".join(missing),
                missing,
            )
        from doc_impact import compute_impact

        reviewed_by = {
            d: {"reviewed_inputs": record["evidence"], "document_sha256": digest(record["document"])}
            for d, record in accepted.items()
            if record is not None
        }
        impact = compute_impact(links, reviewed_by, envelope)
        required = {
            r["section_id"]
            for r in impact["sections"]
            if r["state"] in ("unverified", "needs_review") and r["section_id"] in all_sections
        }
        if not required.issubset(reviewed):
            raise ValueError("Incomplete section review")
    return links


def create_proposal(root: Path, request_path: str, output: str) -> dict:
    control = open_view(root, "worktree")
    request = schema(
        load_json(_required(control, path(request_path), MAX_BUNDLE), MAX_BUNDLE),
        "proposal-request",
        REQUEST_KEYS,
    )
    common(request)
    path(request["candidate_path"])
    envelope = request["evidence"]
    if evidence_id(envelope) != envelope["evidence_id"]:
        raise ValueError("Evidence identity mismatch")
    if request["project_id"] != envelope["project_id"] or request["expected_parents"] != envelope[
        "heads"
    ].get(request["document_id"]):
        raise ValueError("Request evidence binding mismatch")
    candidate = _required(control, request["candidate_path"], MAX_DOCUMENT)
    candidate.decode("utf-8")
    evidence_raw = canonical(envelope) + b"\n"
    manifest = {k: v for k, v in request.items() if k not in ("candidate_path", "evidence")}
    manifest.update(
        schema="agent-docs-kit/proposal",
        document=descriptor("document.md", candidate),
        evidence=descriptor("evidence.json", evidence_raw),
        proposal_id="0" * 64,
    )
    manifest["proposal_id"] = proposal_id(manifest, envelope)
    manifest_raw = canonical(manifest) + b"\n"
    if len(candidate) + len(evidence_raw) + len(manifest_raw) > MAX_BUNDLE:
        raise ValueError("Bundle size limit exceeded")
    output = path(output)
    target = living._safe_path(control.root / output)
    with knowledge_lock(control.root):
        # Identical completed retry precedes freshness, never rewriting bytes.
        if target.exists():
            previous = load_proposal(control, output)
            if previous["manifest"]["proposal_id"] != manifest["proposal_id"]:
                raise FileExistsError("Different proposal at destination")
            control.assert_unchanged()
            return previous["manifest"]
        reader = open_view(root, envelope["view"])
        links = parse_links(_required(reader, envelope["map"]["path"], MAX_DOCUMENT))
        context, accepted = resolved_context(reader, links)
        live = live_heads(root, links, evidence_view=reader)
        if live != context["heads"]:
            raise ReviewRequired("parent_selection_changed", "Live history differs from analysis")
        validate_candidate(reader, request, candidate, context, accepted)
        reader.assert_unchanged()
        control.assert_unchanged()
        import history

        history._mkdirs(control.root, target.parent)
        target.mkdir()  # exclusive; failures leave the exact incomplete bundle
        write_new(target / "document.md", candidate)
        write_new(target / "evidence.json", evidence_raw)
        write_new(target / "proposal.json", manifest_raw)  # publication marker LAST
    return manifest


REVIEW_KEYS = {
    "schema",
    "schema_version",
    "proposal_id",
    "decision",
    "reviewer",
    "reason",
    "review_reference",
    "constraint_authorizations",
}
ACCEPTANCE_KEYS = {
    "schema",
    "schema_version",
    "project_id",
    "document_id",
    "proposal",
    "revision",
    "review",
    "parents",
    "evidence_id",
    "reviewer_declared",
    "review_reference",
}


def reviewer(value):
    obj(value, {"kind", "id"})
    if value["kind"] not in ("human", "agent"):
        raise ValueError("Invalid declared reviewer kind")
    text(value["id"])
    return value


def validate_review(value):
    schema(value, "review", REVIEW_KEYS)
    _hash(value["proposal_id"])
    if value["decision"] != "accept":
        raise ValueError("Review decision must be accept")
    reviewer(value["reviewer"])
    text(value["reason"])
    text(value["review_reference"])
    if not isinstance(value["constraint_authorizations"], list):
        raise ValueError("Invalid constraint authorizations")
    ids = []
    for auth in value["constraint_authorizations"]:
        obj(auth, {"section_id", "reference", "reason"})
        ids.append(identifier(auth["section_id"]))
        text(auth["reference"])
        text(auth["reason"])
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate constraint authorization")
    return value


def validate_acceptance(value):
    schema(value, "acceptance", ACCEPTANCE_KEYS)
    _uuid(value["project_id"])
    identifier(value["document_id"])
    obj(value["proposal"], {"path", "sha256", "proposal_id"})
    path(value["proposal"]["path"])
    _hash(value["proposal"]["sha256"])
    _hash(value["proposal"]["proposal_id"])
    validate_descriptor(value["revision"])
    validate_descriptor(value["review"])
    _sorted(value["parents"], _uuid)
    _hash(value["evidence_id"])
    reviewer(value["reviewer_declared"])
    text(value["review_reference"])
    return value


def accepted_bundle(view, event, *, evidence_view=None):
    payload = validate_acceptance(event["payload"])
    ref = payload["proposal"]
    suffix = "/proposal.json"
    if not ref["path"].endswith(suffix):
        raise ValueError("Acceptance must reference bundle manifest")
    bundle = load_proposal(view, ref["path"][: -len(suffix)])
    manifest = bundle["manifest"]
    env = bundle["evidence"]
    if digest(bundle["manifest_bytes"]) != ref["sha256"] or manifest["proposal_id"] != ref["proposal_id"]:
        raise ValueError("Accepted proposal integrity mismatch")
    expected_revision = dict(manifest["document"], path=bundle["bundle_path"] + "/document.md")
    if payload["revision"] != expected_revision:
        raise ValueError("Accepted revision binding mismatch")
    review_raw = verified(view, payload["review"], MAX_DOCUMENT)
    review = validate_review(load_json(review_raw, MAX_DOCUMENT))
    expected_review = (
        HISTORY.removesuffix("/history")
        + "/reviews/"
        + event["session_id"]
        + "/"
        + digest(event["idempotency_key"].encode("utf-8"))
        + ".json"
    )
    if payload["review"]["path"] != expected_review:
        raise ValueError("Accepted review location mismatch")
    if (
        review["proposal_id"] != manifest["proposal_id"]
        or review["reviewer"] != payload["reviewer_declared"]
        or review["review_reference"] != payload["review_reference"]
        or payload["parents"] != manifest["expected_parents"]
        or payload["project_id"] != manifest["project_id"]
        or payload["document_id"] != manifest["document_id"]
        or payload["evidence_id"] != env["evidence_id"]
    ):
        raise ValueError("Acceptance artifact binding mismatch")
    for key, limit in (("map", MAX_DOCUMENT), ("baseline", MAX_EVIDENCE)):
        raw = _required(evidence_view or view, env[key]["path"], limit)
        if digest(raw) != env[key]["sha256"]:
            raise ValueError("Accepted provenance integrity mismatch")
        if key == "map":
            parse_links(raw)
        else:
            living._validate_manifest(load_json(raw, limit))
    bundle["event"] = event
    bundle["review"] = review
    return bundle


def load_acceptances(view, history_path, *, evidence_view=None):
    import history

    path(history_path)
    events = []
    seen = set()
    total = 0
    for name in view.history_paths(history_path):
        if not name.endswith(".jsonl"):
            continue
        if "/" in name[len(history_path) + 1 :]:
            raise ValueError("Nested history segment unsupported")
        session = _uuid(Path(name).stem)
        raw = _required(view, name, history.MAX_SEGMENT_BYTES)
        total += len(raw)
        if total > history.MAX_SEGMENT_BYTES:
            raise ValueError("Knowledge history read budget exceeded")
        for event in history.parse_segment(raw, expected_session=session):
            if event["event_id"] in seen:
                raise ValueError("Duplicate event ID across sessions")
            seen.add(event["event_id"])
            if event["kind"] == "knowledge.accepted":
                validate_acceptance(event["payload"])
                accepted_bundle(view, event, evidence_view=evidence_view)
                events.append(event)
                if len(events) > 4096:
                    raise ValueError("Knowledge event count limit exceeded")
    # Check every document DAG, including ancestors of current selections.
    for project, did in sorted({(e["payload"]["project_id"], e["payload"]["document_id"]) for e in events}):
        resolve_acceptances(events, project, did)
    return events


def resolve_acceptances(events, project_id, document_id):
    import history

    _uuid(project_id)
    identifier(document_id)
    all_events = {}
    for event in events:
        history._event(event, None)
        if event["kind"] != "knowledge.accepted":
            raise ValueError("Expected acceptance event")
        validate_acceptance(event["payload"])
        if event["event_id"] in all_events:
            raise ValueError("Duplicate event ID")
        all_events[event["event_id"]] = event
    nodes = {
        eid: e
        for eid, e in all_events.items()
        if (e["payload"]["project_id"], e["payload"]["document_id"]) == (project_id, document_id)
    }
    children = set()
    for event in nodes.values():
        for parent in event["payload"]["parents"]:
            if parent not in nodes:
                raise ValueError("Missing or foreign acceptance parent")
            children.add(parent)
    # Iterative topological elimination is bounded and does not trust clock order.
    remaining = {eid: set(e["payload"]["parents"]) for eid, e in nodes.items()}
    done = set()
    while remaining:
        ready = {eid for eid, parents in remaining.items() if parents <= done}
        if not ready:
            raise ValueError("Acceptance cycle")
        done.update(ready)
        for eid in ready:
            del remaining[eid]
    heads = sorted(set(nodes) - children)
    state = "unaccepted" if not heads else ("accepted" if len(heads) == 1 else "conflict")
    return {
        "selection": state,
        "heads": heads,
        "selected": nodes[heads[0]] if len(heads) == 1 else None,
        "problem_codes": [],
    }


def resolved_context(view, links, history_path=HISTORY, *, origin_view=None):
    events = load_acceptances(view, history_path, evidence_view=origin_view)
    project = links["project_id"]
    ids = {d["id"] for d in links["documents"]}
    if {e["payload"]["document_id"] for e in events if e["payload"]["project_id"] == project} - ids:
        raise ValueError("Unsupported accepted document retirement")
    context = {k: {} for k in ("base_documents", "heads", "proposal_ids", "selection")}
    accepted = {}
    for spec in links["documents"]:
        did = spec["id"]
        result = resolve_acceptances(events, project, did)
        context["heads"][did] = result["heads"]
        context["selection"][did] = result["selection"]
        if not result["heads"]:
            context["base_documents"][did] = _required(origin_view or view, spec["origin"], MAX_DOCUMENT)
            context["proposal_ids"][did] = None
            continue
        parents = [
            accepted_bundle(view, next(e for e in events if e["event_id"] == eid), evidence_view=origin_view)
            for eid in result["heads"]
        ]
        anchor = parents[0]  # heads sorted: comparison anchor, never conflict selection
        context["base_documents"][did] = anchor["document"]
        context["proposal_ids"][did] = anchor["manifest"]["proposal_id"]
        accepted[did] = dict(anchor, parent_bundles=parents)
    return context, accepted


def live_heads(root, links, *, evidence_view=None):
    events = load_acceptances(open_view(root, "worktree"), HISTORY, evidence_view=evidence_view)
    ids = {d["id"] for d in links["documents"]}
    if {
        e["payload"]["document_id"] for e in events if e["payload"]["project_id"] == links["project_id"]
    } - ids:
        raise ValueError("Unsupported accepted document retirement")
    return {did: resolve_acceptances(events, links["project_id"], did)["heads"] for did in ids}


def constraint_changes(reader, links, did, candidate, parents):
    spec = next(d for d in links["documents"] if d["id"] == did)
    new_spans = section_spans(candidate, spec)["spans"]
    new_sections = {s["id"]: s for s in spec["sections"]}
    changed = set()
    for parent in parents:
        ref = parent["evidence"]["map"]
        raw = _required(reader, ref["path"], MAX_DOCUMENT)
        if digest(raw) != ref["sha256"]:
            raise ValueError("Old map integrity mismatch")
        old_map = parse_links(raw)
        old = next(d for d in old_map["documents"] if d["id"] == did)
        old_spans = section_spans(parent["document"], old)["spans"]
        for section in old["sections"]:
            if section["claim_kind"] != "constraint":
                continue
            sid = section["id"]
            start, end = old_spans[sid]
            new = new_sections.get(sid)
            if new is None or new["claim_kind"] != "constraint":
                changed.add(sid)
                continue
            a, b = new_spans[sid]
            if parent["document"][start:end] != candidate[a:b]:
                changed.add(sid)
    return changed


def publisher_freshness(root, bundle):
    """Explicit live control artifacts; evaluated sources stay in the origin view.

    Reusing the already-verified original comparison bases here checks source
    freshness without requiring the just-published bundle to be staged. Readers
    use selected-phase revalidation and never use this publisher-only projection.
    """
    env = bundle["evidence"]
    reader = open_view(root, env["view"])
    raw = _required(reader, env["map"]["path"], MAX_DOCUMENT)
    links = parse_links(raw)
    # Live accepted artifact bytes are explicit publisher control state.
    live, records = resolved_context(open_view(root, "worktree"), links, origin_view=reader)
    if digest(_required(reader, env["baseline"]["path"], MAX_EVIDENCE)) != env["baseline"]["sha256"]:
        raise ValueError("Original baseline integrity mismatch")
    from doc_evidence import capture_evidence, external_dependencies

    current = capture_evidence(
        reader,
        links,
        env["baseline"]["path"],
        env["map"]["path"],
        live["base_documents"],
        live["heads"],
        proposal_ids=live["proposal_ids"],
    )
    reasons = []
    for key in ("map", "files", "inventory"):
        if current[key] != env[key]:
            reasons.append(key + "_changed")
    old = {d["document_id"]: d["sha256"] for d in env["base_documents"]}
    did = bundle["manifest"]["document_id"]
    for dependency in external_dependencies(links, did):
        if live["selection"][dependency] != "accepted":
            reasons.append("dependency_selection_unresolved")
        if digest(live["base_documents"][dependency]) != old.get(dependency):
            reasons.append("dependency_document_changed")
    if (
        live["selection"][did] != "accepted"
        or records.get(did, {}).get("manifest", {}).get("proposal_id") != bundle["manifest"]["proposal_id"]
    ):
        reasons.append("selection_changed")
    reader.assert_unchanged()
    return {
        "state": "stale" if reasons else "matches_review",
        "reasons": sorted(set(reasons)),
        "view": env["view"],
    }


def accept_proposal(root, proposal_path, review_path, session, key, *, confirm_reviewed, timeout=5.0):
    if confirm_reviewed is not True:
        raise ValueError("Explicit confirm-reviewed required")
    _uuid(session)
    text(key)
    control = open_view(root, "worktree")
    bundle = load_proposal(control, path(proposal_path))
    review_raw = _required(control, path(review_path), MAX_DOCUMENT)
    review = validate_review(load_json(review_raw, MAX_DOCUMENT))
    manifest = bundle["manifest"]
    env = bundle["evidence"]
    did = manifest["document_id"]
    if review["proposal_id"] != manifest["proposal_id"]:
        raise ValueError("Review proposal mismatch")
    review_target = "docs/living/reviews/" + session + "/" + digest(key.encode("utf-8")) + ".json"
    payload = {
        "schema": "agent-docs-kit/acceptance",
        "schema_version": 1,
        "project_id": manifest["project_id"],
        "document_id": did,
        "proposal": {
            "path": bundle["bundle_path"] + "/proposal.json",
            "sha256": digest(bundle["manifest_bytes"]),
            "proposal_id": manifest["proposal_id"],
        },
        "revision": dict(manifest["document"], path=bundle["bundle_path"] + "/document.md"),
        "review": descriptor(review_target, review_raw),
        "parents": manifest["expected_parents"],
        "evidence_id": env["evidence_id"],
        "reviewer_declared": review["reviewer"],
        "review_reference": review["review_reference"],
    }
    validate_acceptance(payload)
    import history

    with knowledge_lock(control.root, timeout):
        live_view = open_view(root, "worktree")
        # Include other event kinds: a reused history key cannot evade conflict.
        segment = live_view.read(HISTORY + "/" + session + ".jsonl", maximum=history.MAX_SEGMENT_BYTES)
        if segment is not None:
            for event in history.parse_segment(segment, expected_session=session):
                if event["idempotency_key"] == key:
                    if (
                        event["kind"] != "knowledge.accepted"
                        or event["payload"] != payload
                        or event["source"] != "knowledge"
                    ):
                        raise ValueError("Idempotency key conflict")
                    accepted_bundle(live_view, event, evidence_view=open_view(root, env["view"]))
                    # Re-enter v1's identical retry path: durable ACK requires
                    # fsync even when an earlier append had unknown outcome.
                    receipt = history.append_event(
                        control.root,
                        HISTORY,
                        session,
                        "knowledge.accepted",
                        payload,
                        key,
                        source="knowledge",
                        timeout=timeout,
                    )
                    if receipt != event:
                        raise OSError("History retry receipt changed")
                    freshness = publisher_freshness(root, bundle)
                    return {
                        "state": "recorded",
                        "event": event,
                        "freshness": freshness,
                        "retry": True,
                        "exit_code": 0,
                        "semantic_review": "not_attested",
                    }
        reader = open_view(root, env["view"])
        links = parse_links(_required(reader, env["map"]["path"], MAX_DOCUMENT))
        context, accepted = resolved_context(reader, links)
        if live_heads(root, links, evidence_view=reader) != context["heads"]:
            raise ReviewRequired("parent_selection_changed", "Live history differs from analysis")
        request = {k: v for k, v in manifest.items() if k not in ("document", "proposal_id")}
        request["evidence"] = env
        validate_candidate(reader, request, bundle["document"], context, accepted)
        if not context["heads"][did] and manifest["mode"] != "bootstrap":
            raise ValueError("Initial acceptance requires bootstrap")
        parent_bundles = accepted.get(did, {}).get("parent_bundles", [])
        needed = constraint_changes(reader, links, did, bundle["document"], parent_bundles)
        if not needed <= {a["section_id"] for a in review["constraint_authorizations"]}:
            raise ValueError("Specific old constraint authorization required")
        control.assert_unchanged()
        reader.assert_unchanged()
        target = living._safe_path(control.root / review_target)
        history._mkdirs(control.root, target.parent)
        if target.exists():
            if _required(open_view(root, "worktree"), review_target, MAX_DOCUMENT) != review_raw:
                raise ValueError("Preserved review conflict")
        else:
            write_new(target, review_raw)
        # Original history v1 writer owns its own session lock and unknown outcome.
        event = history.append_event(
            control.root,
            HISTORY,
            session,
            "knowledge.accepted",
            payload,
            key,
            source="knowledge",
            timeout=timeout,
        )
        freshness = publisher_freshness(root, bundle)
        state = "recorded" if freshness["state"] == "matches_review" else "recorded_but_stale"
        return {
            "state": state,
            "event": event,
            "freshness": freshness,
            "retry": False,
            "exit_code": 0 if state == "recorded" else 1,
            "semantic_review": "not_attested",
        }
