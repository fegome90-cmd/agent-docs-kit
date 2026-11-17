"""Read-only, view-bound raw evidence. Race checks are not a filesystem transaction."""
from __future__ import annotations
import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import re
import uuid
from typing import Any
import living_docs as living
from git_gate import _Git, _read_index, _Entry, _included, _decode, _index_path
from doc_links import canonical, load_json, obj, path, parse_links, identifier, unique_list

MAX_EVIDENCE = 8 * 1024 * 1024
MAX_SOURCE_BYTES = 64 * 1024 * 1024  # Explicit raw-source ceiling, never truncate.
HASH = re.compile(r'[0-9a-f]{64}\Z')
DETECTOR_VERSION = 'knowledge/1'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _hash(value):
    if not isinstance(value, str) or not HASH.fullmatch(value): raise ValueError('Invalid SHA-256')
    return value


def _sorted(value, validate):
    result = unique_list(value, validate)
    if result != sorted(result): raise ValueError('Set must be sorted')
    return result


def _uuid(value):
    try:
        if str(uuid.UUID(value)) != value: raise ValueError('Noncanonical UUID')
    except (ValueError, AttributeError, TypeError) as exc: raise ValueError('Invalid UUID') from exc
    return value


class EvidenceView:
    def __init__(self, git, raw_index, entries, head, kind):
        self.root = git.root
        self.kind = kind
        self._git = git
        self._index = raw_index
        self._entries = dict(entries)
        self._head = head
        self._observed = {}
        self._inventories = {}
        self._history_inventories = {}

    def _record(self, relative, *, maximum=None):
        relative = path(relative)
        limit = MAX_SOURCE_BYTES if maximum is None else maximum
        if self.kind == 'index':
            entry = self._entries.get(relative)
            if entry is None:
                if any(p.startswith(relative + '/') for p in self._entries): raise ValueError('Expected file, found directory')
                record, data = {'path': relative, 'present': False, 'size': None, 'sha256': None}, None
            else:
                data = self._git.blob(entry, maximum=limit)
                record = {'path': relative, 'present': True, 'size': len(data), 'sha256': digest(data)}
        else:
            target = living._safe_path(self.root / relative)
            kind = living._kind(target, missing_ok=True)
            if kind == 'missing':
                record, data = {'path': relative, 'present': False, 'size': None, 'sha256': None}, None
            else:
                if kind != 'file': raise ValueError('Expected regular file')
                if target.stat().st_size > limit: raise ValueError('File size limit exceeded')
                chunks = [] if maximum is not None else None
                record = dict(living._fingerprint(target, relative, chunks=chunks, maximum_bytes=limit), present=True)
                if record['size'] > limit: raise ValueError('File size limit exceeded')
                data = b''.join(chunks) if chunks is not None else None
        return record, data

    def _remember(self, relative, record):
        if relative in self._observed and self._observed[relative] != record:
            raise ValueError('File changed during evidence capture: ' + relative)
        self._observed[relative] = record

    def read(self, path: str, *, maximum: int) -> bytes | None:
        if type(maximum) is not int or maximum < 0: raise ValueError('Invalid read limit')
        record, data = self._record(path, maximum=maximum)
        self._remember(path, record)
        return data

    def fingerprint(self, path: str) -> dict:
        record, _ = self._record(path)
        self._remember(path, record)
        return dict(record)

    def _paths(self, prefix):
        prefix = living._relative(prefix, canonical=True)
        if self.kind == 'index':
            return tuple(sorted(p for p in self._entries if _included(p) and living._within(p, prefix)))
        raw = self._git.run('ls-files', '--cached', '--others', '--exclude-standard', '-z', '--', prefix)
        if raw and not raw.endswith(b'\0'): raise ValueError('Incomplete Git inventory')
        values = set()
        for item in raw.split(b'\0'):
            if not item: continue
            p = _index_path(_decode(item, 'inventory path'))
            if _included(p) and living._within(p, prefix):
                kind = living._kind(self.root / p, missing_ok=True)
                if kind not in ('file', 'missing'): raise ValueError('Nonregular inventory candidate')
                # Tracked deletion remains an eligible, explicitly absent path.
                values.add(p)
        return tuple(sorted(values))

    def paths(self, prefix: str) -> tuple[str, ...]:
        values = self._paths(prefix)
        if prefix in self._inventories and self._inventories[prefix] != values: raise ValueError('Inventory changed')
        self._inventories[prefix] = values
        return values

    def _history_paths(self, prefix):
        # Explicit authoritative storage is not filtered by discovery ignore rules.
        import history
        prefix = path(prefix)
        _, directory, _ = history._location(self.root, prefix)
        kind = living._kind(directory, missing_ok=True)
        if kind == 'missing': return ()
        if kind != 'directory': raise ValueError('History path must be a directory')
        before = directory.stat()
        values = []
        entries = []
        with os.scandir(directory) as iterator:
            for entry in iterator:
                if len(entries) >= 16384:
                    raise ValueError('History directory entry budget exceeded')
                entries.append(entry)
        for entry in entries:
            target = directory / entry.name
            if entry.is_dir(follow_symlinks=False):
                raise ValueError('Nested history directory unsupported')
            if not entry.name.endswith('.jsonl'): continue
            history._uuid(entry.name[:-6], 'History segment name')
            history._regular(target)
            values.append(prefix + '/' + entry.name)
        if history._signature(directory.stat()) != history._signature(before):
            raise ValueError('History directory changed during enumeration')
        return tuple(sorted(values))

    def history_paths(self, prefix: str) -> tuple[str, ...]:
        if self.kind == 'index': return self.paths(prefix)
        values = self._history_paths(prefix)
        if prefix in self._history_inventories and self._history_inventories[prefix] != values:
            raise ValueError('Explicit history inventory changed')
        self._history_inventories[prefix] = values
        return values

    def assert_unchanged(self) -> None:
        if _read_index(self._git)[0] != self._index or self._git.head() != self._head:
            raise ValueError('Index or HEAD changed during evidence capture')
        for relative, before in self._observed.items():
            if self._record(relative)[0] != before: raise ValueError('File changed during evidence capture: ' + relative)
        for prefix, before in self._inventories.items():
            if self._paths(prefix) != before: raise ValueError('Inventory changed during evidence capture')
        for prefix, before in self._history_inventories.items():
            if self._history_paths(prefix) != before: raise ValueError('Explicit history inventory changed during evidence capture')


def from_index(git: _Git, raw_index: bytes, entries: dict[str, _Entry], head: str | None) -> EvidenceView:
    return EvidenceView(git, raw_index, entries, head, 'index')


def open_view(root: Path, kind: str) -> EvidenceView:
    if kind not in ('worktree', 'index'): raise ValueError('Unknown evidence view')
    git = _Git(living._root(root))
    raw, entries = _read_index(git)
    return EvidenceView(git, raw, entries, git.head(), kind)


def validate_evidence(payload):
    # Validate complete nested schemas before any identity comparison.
    if len(canonical(payload)) > MAX_EVIDENCE: raise ValueError('Evidence size limit exceeded')
    from doc_links import depth
    depth(payload)
    obj(payload, {'schema', 'schema_version', 'project_id', 'detector_version', 'view', 'map', 'baseline',
                  'base_documents', 'heads', 'files', 'inventory', 'observed_at', 'evidence_id'})
    if (payload['schema'] != 'agent-docs-kit/evidence' or type(payload['schema_version']) is not int
            or payload['schema_version'] != 1 or payload['detector_version'] != DETECTOR_VERSION
            or payload['view'] not in ('worktree', 'index')): raise ValueError('Unsupported evidence schema')
    _uuid(payload['project_id']); _hash(payload['evidence_id'])
    try:
        stamp = payload['observed_at']
        if (not isinstance(stamp, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z', stamp)):
            raise ValueError('Invalid observation timestamp')
        datetime.fromisoformat(stamp)
    except (TypeError, OverflowError) as exc: raise ValueError('Invalid observation timestamp') from exc
    for key in ('map', 'baseline'):
        obj(payload[key], {'path', 'sha256'}); path(payload[key]['path']); _hash(payload[key]['sha256'])
    if not isinstance(payload['files'], list): raise ValueError('Expected file records')
    names = []
    for record in payload['files']:
        obj(record, {'path', 'present', 'size', 'sha256'}); names.append(path(record['path']))
        if type(record['present']) is not bool: raise ValueError('Invalid presence')
        if record['present']:
            if type(record['size']) is not int or record['size'] < 0: raise ValueError('Invalid byte size')
            _hash(record['sha256'])
        elif record['size'] is not None or record['sha256'] is not None: raise ValueError('Absent record has content')
    if names != sorted(set(names)): raise ValueError('File records must be unique and sorted')
    if not isinstance(payload['base_documents'], list) or len(payload['base_documents']) > 64: raise ValueError('Invalid base documents')
    names = []
    for record in payload['base_documents']:
        obj(record, {'document_id', 'proposal_id', 'sha256'})
        names.append(identifier(record['document_id'])); _hash(record['sha256'])
        if record['proposal_id'] is not None: _hash(record['proposal_id'])
    if names != sorted(set(names)): raise ValueError('Base documents must be unique and sorted')
    if not isinstance(payload['heads'], dict) or set(payload['heads']) != set(names): raise ValueError('Heads must cover base documents')
    for key, values in payload['heads'].items(): identifier(key); _sorted(values, _uuid)
    inventory = obj(payload['inventory'], {'policy_sha256', 'eligible_paths', 'not_evaluated'})
    _hash(inventory['policy_sha256']); _sorted(inventory['eligible_paths'], path); _sorted(inventory['not_evaluated'], path)
    if set(inventory['not_evaluated']) - set(inventory['eligible_paths']): raise ValueError('Unevaluated paths outside inventory')
    return payload


def evidence_id(payload: dict) -> str:
    validate_evidence(payload)
    body = {k: v for k, v in payload.items() if k not in ('evidence_id', 'observed_at')}
    return digest(b'agent-docs-kit/evidence/v1\0' + canonical(body))


def _required(view, relative, maximum):
    raw = view.read(relative, maximum=maximum)
    if raw is None: raise ValueError('Required evidence input missing: ' + relative)
    return raw


def capture_evidence(view: EvidenceView, links: dict, snapshot_path: str, map_path: str,
                     base_documents: dict[str, bytes], heads: dict[str, list[str]], *,
                     proposal_ids: dict[str, str | None] | None = None) -> dict:
    raw_map = _required(view, map_path, 1048576)
    actual = parse_links(raw_map)
    if actual != links: raise ValueError('Map argument differs from selected view')
    raw_snapshot = _required(view, snapshot_path, MAX_EVIDENCE)
    baseline = living._validate_manifest(load_json(raw_snapshot, MAX_EVIDENCE))
    document_ids = {d['id'] for d in links['documents']}
    if set(base_documents) != document_ids or set(heads) != document_ids: raise ValueError('Incomplete document evidence')
    docs = []
    for document in sorted(links['documents'], key=lambda d: d['id']):
        raw = base_documents[document['id']]
        if not isinstance(raw, bytes) or len(raw) > 1048576: raise ValueError('Invalid base document')
        # Bootstrap binds actual origin bytes. Selected immutable revisions are
        # supplied by the later resolver; no proposal writer exists at Gate A.
        if not heads[document['id']] and _required(view, document['origin'], 1048576) != raw:
            raise ValueError('Base document differs from view')
        docs.append({'document_id': document['id'], 'proposal_id': (proposal_ids or {}).get(document['id']), 'sha256': digest(raw)})
    selected = {r['path'] for r in baseline['sources']}
    selected.update(p for d in links['documents'] for s in d['sections'] for p in s['watches'])
    selected.update(item['path'] for item in links['inventory_only'])
    policy = {'source_scopes': baseline['source_scopes'], 'discovery': baseline.get('discovery'),
              'exclusions': baseline['exclusions'], 'inventory_only': links['inventory_only']}
    eligible = set()
    if baseline.get('discovery'):
        from discovery import validate_roots
        if view.kind == 'worktree': validate_roots(view.root, baseline['discovery'])
        for root in baseline['discovery']['roots']: eligible.update(view.paths(root))
    else:
        for scope in baseline['source_scopes']:
            if scope['kind'] == 'directory': eligible.update(view.paths(scope['path']))
            else: eligible.add(scope['path'])
    # Explicitly selected sources also need a visible relationship disposition.
    eligible.update(selected)
    linked = {p for d in links['documents'] for s in d['sections'] for p in s['watches']}
    disposed = {item['path'] for item in links['inventory_only']}
    result = {'schema': 'agent-docs-kit/evidence', 'schema_version': 1, 'project_id': links['project_id'],
              'detector_version': DETECTOR_VERSION, 'view': view.kind,
              'map': {'path': map_path, 'sha256': digest(raw_map)},
              'baseline': {'path': snapshot_path, 'sha256': digest(raw_snapshot)},
              'base_documents': docs, 'heads': {k: sorted(v) for k, v in heads.items()},
              'files': [view.fingerprint(p) for p in sorted(selected)],
              'inventory': {'policy_sha256': digest(canonical(policy)), 'eligible_paths': sorted(eligible),
                            'not_evaluated': sorted(eligible - linked - disposed)},
              'observed_at': datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
              'evidence_id': '0' * 64}
    result['evidence_id'] = evidence_id(result)
    view.assert_unchanged()
    return result


def content_inputs(envelope):
    validate_evidence(envelope)
    return {key: envelope[key] for key in ('project_id', 'map', 'files', 'inventory', 'base_documents', 'heads')}


def external_dependencies(links, document_id):
    sections = {s['id']: s for d in links['documents'] for s in d['sections']}
    owners = {s['id']: d['id'] for d in links['documents'] for s in d['sections']}
    pending = [s['id'] for d in links['documents'] if d['id'] == document_id for s in d['sections']]
    seen = set()
    while pending:
        sid = pending.pop()
        if sid in seen: continue
        seen.add(sid)
        pending.extend(sections[sid]['depends_on'])
    return {owners[sid] for sid in seen} - {document_id}


def revalidate(view: EvidenceView, envelope: dict, *, phase: str, selected: dict | None = None,
               resolved: dict | None = None, snapshot_path: str | None = None) -> dict:
    if phase not in ('proposal', 'selected'): raise ValueError('Unknown revalidation phase')
    if evidence_id(envelope) != envelope['evidence_id']: raise ValueError('Evidence identity mismatch')
    # Provenance is immutable even when a new comparison snapshot is supplied.
    if digest(_required(view, envelope['baseline']['path'], MAX_EVIDENCE)) != envelope['baseline']['sha256']:
        raise ValueError('Original baseline integrity mismatch')
    reasons = []
    if phase == 'proposal' and view.kind != envelope['view']: reasons.append('view_changed')
    if phase == 'proposal' and snapshot_path is not None and snapshot_path != envelope['baseline']['path']:
        reasons.append('baseline_changed')
    raw_map = _required(view, envelope['map']['path'], 1048576)
    links = parse_links(raw_map)
    if digest(raw_map) != envelope['map']['sha256']: reasons.append('map_changed')
    if links['project_id'] != envelope['project_id']: reasons.append('project_changed')
    ids = {d['id'] for d in links['documents']}
    if resolved is None:
        if any(envelope['heads'].values()) or any(d['proposal_id'] for d in envelope['base_documents']):
            raise ValueError('Accepted evidence requires verified resolved context')
        resolved = {'base_documents': {d['id']: _required(view, d['origin'], 1048576) for d in links['documents']},
                    'heads': {did: [] for did in ids}, 'proposal_ids': {did: None for did in ids},
                    'selection': {did: 'unaccepted' for did in ids}}
    obj(resolved, {'base_documents', 'heads', 'proposal_ids', 'selection'})
    for key in resolved:
        if not isinstance(resolved[key], dict) or set(resolved[key]) != ids:
            raise ValueError('Incomplete resolved context')
    for did in ids:
        _sorted(resolved['heads'][did], _uuid)
        pid = resolved['proposal_ids'][did]
        if pid is not None: _hash(pid)
        state = resolved['selection'][did]
        if state not in ('accepted', 'conflict', 'unaccepted'): raise ValueError('Invalid selection')
        if (state == 'accepted' and (len(resolved['heads'][did]) != 1 or pid is None)
                or state == 'conflict' and len(resolved['heads'][did]) < 2
                or state == 'unaccepted' and (resolved['heads'][did] or pid is not None)):
            raise ValueError('Inconsistent resolved selection')
    current = capture_evidence(view, links, snapshot_path or envelope['baseline']['path'], envelope['map']['path'],
                               resolved['base_documents'], resolved['heads'], proposal_ids=resolved['proposal_ids'])
    for key in ('files', 'inventory'):
        if current[key] != envelope[key]: reasons.append(key + '_changed')
    if phase == 'proposal':
        for key in ('base_documents', 'heads'):
            if current[key] != envelope[key]: reasons.append(key + '_changed')
    else:
        if selected is None: raise ValueError('Selected revalidation requires exact document binding')
        obj(selected, {'document_id', 'path', 'sha256'})
        identifier(selected['document_id']); _hash(selected['sha256'])
        did = selected['document_id']
        if did not in ids: raise ValueError('Selected document is not in the map')
        if digest(_required(view, selected['path'], 1048576)) != selected['sha256']: reasons.append('selected_document_changed')
        old = {d['document_id']: d['sha256'] for d in envelope['base_documents']}
        for dependency in external_dependencies(links, did):
            if resolved['selection'][dependency] != 'accepted': reasons.append('dependency_selection_unresolved')
            if digest(resolved['base_documents'][dependency]) != old.get(dependency):
                reasons.append('dependency_document_changed')
    view.assert_unchanged()
    return {'state': 'stale' if reasons else 'matches_review', 'reasons': sorted(set(reasons)),
            'origin_view': envelope['view'], 'view': view.kind, 'evidence_id': current['evidence_id']}
