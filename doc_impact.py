"""Deterministic review obligations, never semantic approval or prose generation."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from doc_links import canonical, parse_links, ordered_sections, section_spans, path
from doc_evidence import (EvidenceView, open_view, capture_evidence, evidence_id,
                          MAX_EVIDENCE, _required)


def _envelope(value):
    if not isinstance(value, dict): raise ValueError('Expected evidence object')
    result = value.get('reviewed_inputs', value)
    if evidence_id(result) != result['evidence_id']: raise ValueError('Corrupt evidence identity')
    return result


def compute_impact(links: dict, reviewed_by_document: dict[str, dict | None], current: dict) -> dict:
    links = parse_links(canonical(links))
    evidence = _envelope(current.get('evidence', current))
    if evidence['project_id'] != links['project_id']: raise ValueError('Evidence belongs to another project')
    if not isinstance(reviewed_by_document, dict): raise ValueError('Expected reviewed documents')
    documents = {d['id']: d for d in links['documents']}
    if set(reviewed_by_document) - documents.keys(): raise ValueError('Unknown reviewed document')
    order = ordered_sections(links)
    sections = {s['id']: s for d in links['documents'] for s in d['sections']}
    owners = {s['id']: d['id'] for d in links['documents'] for s in d['sections']}
    reviewed = {}
    for did in documents:
        value = reviewed_by_document.get(did)
        reviewed[did] = None if value is None else _envelope(value)
        if reviewed[did] is not None and reviewed[did]['project_id'] != links['project_id']:
            raise ValueError('Reviewed evidence belongs to another project')
    files = {f['path']: f for f in evidence['files']}
    bases = {d['document_id']: d for d in evidence['base_documents']}
    reasons = {sid: set() for sid in sections}
    reason_bytes = 0

    def add(sid, code, source=None, via=None):
        nonlocal reason_bytes
        reason = (code, source, via)
        if reason not in reasons[sid]:
            reason_bytes += len(canonical({'code': code, 'path': source, 'via': via}))
            if reason_bytes > MAX_EVIDENCE: raise ValueError('Impact causes exceed evidence output budget')
            reasons[sid].add(reason)

    for sid in order:
        section = sections[sid]
        before = reviewed[owners[sid]]
        if before is None:
            add(sid, 'unaccepted')
        else:
            old_files = {f['path']: f for f in before['files']}
            old_docs = {d['document_id']: d for d in before['base_documents']}
            if evidence['map'] != before['map']: add(sid, 'map_changed', evidence['map']['path'])
            if evidence['inventory']['policy_sha256'] != before['inventory']['policy_sha256']:
                add(sid, 'inventory_policy_changed')
            binding = reviewed_by_document.get(owners[sid], {})
            selected_hash = binding.get('document_sha256') if isinstance(binding, dict) else None
            if selected_hash is None:
                changed = bases.get(owners[sid], {}).get('sha256') != old_docs.get(owners[sid], {}).get('sha256')
            else:
                changed = bases.get(owners[sid], {}).get('sha256') != selected_hash
            if changed: add(sid, 'document_changed')
            for source in section['watches']:
                if source not in files: raise ValueError('Evidence omits a watched source: ' + source)
                if old_files.get(source) != files[source]:
                    add(sid, 'direct_change', source)
                    if section['claim_kind'] == 'constraint': add(sid, 'potential_constraint_conflict', source)
            for dependency in section['depends_on']:
                owner = owners[dependency]
                if owner != owners[sid] and bases.get(owner, {}).get('sha256') != old_docs.get(owner, {}).get('sha256'):
                    add(sid, 'dependency_document_changed', via=dependency)
        for dependency in sorted(section['depends_on']):
            for _, source, _ in sorted(reasons[dependency], key=lambda r: (r[0], r[1] or '', r[2] or '')):
                add(sid, 'dependency_change', source, dependency)

    watched = {p for s in sections.values() for p in s['watches']}
    disposed = {item['path'] for item in links['inventory_only']}
    unmapped = sorted((set(evidence['inventory']['eligible_paths']) | set(files)) - watched - disposed)
    unmapped_sections = current.get('unmapped_sections', {}) if 'evidence' in current else {}
    if not isinstance(unmapped_sections, dict) or set(unmapped_sections) - documents.keys():
        raise ValueError('Invalid unmapped section inventory')
    for headings in unmapped_sections.values():
        if not isinstance(headings, list) or not all(isinstance(h, str) for h in headings):
            raise ValueError('Invalid unmapped headings')
    records = []
    for sid in order:
        state = 'unverified' if reviewed[owners[sid]] is None else ('needs_review' if reasons[sid] else 'no_linked_change')
        records.append({'section_id': sid, 'state': state,
                        'reasons': [{'code': code, 'path': source, 'via': via}
                                    for code, source, via in sorted(reasons[sid], key=lambda r: (r[0], r[1] or '', r[2] or ''))]})
    queue = [s for s in order if reasons[s]]
    partial = bool(unmapped or any(unmapped_sections.values()))
    result = {'schema': 'agent-docs-kit/impact', 'schema_version': 1,
              'state': 'review_required' if queue or partial else 'no_linked_change',
              'semantic_review': 'not_attested',
              'coverage': {'state': 'partial' if partial else 'declared_scope',
                           'eligible_paths': evidence['inventory']['eligible_paths'],
                           'not_evaluated': unmapped,
                           'inventory_only': [dict(item, file=files.get(item['path'])) for item in links['inventory_only']]},
              'unmapped_sources': unmapped, 'unmapped_sections': unmapped_sections,
              'sections': records, 'review_queue': queue, 'evidence': evidence}
    if len(canonical(result)) > MAX_EVIDENCE: raise ValueError('Impact report size limit exceeded')
    return result


def check_knowledge(root: Path, map_path: str, snapshot_path: str, *, view: str = 'worktree',
                    history_path: str = 'docs/living/history', read_view: EvidenceView | None = None) -> dict:
    path(map_path); path(snapshot_path); path(history_path)
    reader = read_view if read_view is not None else open_view(root, view)
    import living_docs as living
    if reader.root != living._root(root) or reader.kind != view: raise ValueError('Reader root or view mismatch')
    links = parse_links(_required(reader, map_path, 1048576))
    from doc_revisions import resolved_context
    from doc_current import selection_metadata
    from doc_evidence import digest
    context, accepted = resolved_context(reader, links, history_path)
    bases = context['base_documents']; unmapped = {}
    for document in links['documents']:
        did = document['id']
        # A changed map is review debt; old accepted spans need not fit it.
        record = accepted.get(did)
        same_map = record is None or record['evidence']['map'] == {'path':map_path, 'sha256':digest(_required(reader,map_path,1048576))}
        if same_map:
            headings = section_spans(bases[did], document)['unmapped_headings']
            if headings: unmapped[did] = headings
    evidence = capture_evidence(reader, links, snapshot_path, map_path, bases, context['heads'], proposal_ids=context['proposal_ids'])
    reviewed = {did:{'reviewed_inputs':record['evidence'], 'document_sha256':digest(record['document'])}
                for did,record in accepted.items() if context['selection'][did]=='accepted'}
    report = compute_impact(links, reviewed, {'evidence':evidence, 'unmapped_sections':unmapped})
    selections = selection_metadata(reader,links,map_path,context,accepted,snapshot_path=snapshot_path)
    report['selection'] = {did:context['selection'][did] for did in sorted(bases)}
    report['freshness'] = {did:selections[did]['freshness'] for did in sorted(bases)}
    if any(record['state']!='current' for record in selections.values()): report['state']='review_required'
    if len(canonical(report))>MAX_EVIDENCE: raise ValueError('Impact report size limit exceeded')
    reader.assert_unchanged()
    return report
