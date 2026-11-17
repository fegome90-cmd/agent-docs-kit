"""Strict curated relationships and exact UTF-8/LF Markdown byte spans."""
from __future__ import annotations
import json
import re
import uuid
from typing import Any
import living_docs as living

MAX_MAP = 1024 * 1024
ID = re.compile(r'[a-z][a-z0-9._-]{0,127}\Z')
HEADING = re.compile(r' {0,3}(#{2,6})(?:[ \t]+.*)?\Z')


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def depth(value, level=0):
    if level > 64:
        raise ValueError('JSON depth exceeds 64')
    if isinstance(value, dict):
        for item in value.values(): depth(item, level + 1)
    elif isinstance(value, list):
        for item in value: depth(item, level + 1)


def load_json(data: bytes, maximum: int):
    if not isinstance(data, bytes) or len(data) > maximum:
        raise ValueError('JSON size limit exceeded')
    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=living._unique_object,
                           parse_constant=living._invalid_constant)
        depth(value)
        return value
    except (UnicodeError, RecursionError) as exc:
        raise ValueError('Invalid UTF-8 or excessive JSON depth') from exc


def obj(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError('Unknown or missing schema fields')
    return value


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('Invalid stable identifier')
    return value


def path(value):
    return living._relative(value, canonical=True, allow_root=False)


def unique_list(value, validate):
    if not isinstance(value, list): raise ValueError('Expected list')
    result = [validate(v) for v in value]
    if len(set(result)) != len(result): raise ValueError('Duplicate list member')
    return result


def validate_document(document):
    obj(document, {'id', 'origin', 'sections'})
    identifier(document['id']); path(document['origin'])
    sections = document['sections']
    if not isinstance(sections, list) or not sections or len(sections) > 512:
        raise ValueError('Invalid section count')
    ids = set()
    for section in sections:
        obj(section, {'id', 'selector', 'claim_kind', 'watches', 'depends_on'})
        sid = identifier(section['id'])
        if sid in ids: raise ValueError('Duplicate section ID')
        ids.add(sid)
        selector = section['selector']
        if not isinstance(selector, dict): raise ValueError('Invalid selector')
        if selector.get('kind') == 'document':
            obj(selector, {'kind'})
            if len(sections) != 1: raise ValueError('Whole document must be exclusive')
        else:
            obj(selector, {'kind', 'heading'})
            heading = selector['heading']
            if (selector['kind'] != 'atx' or not isinstance(heading, str)
                    or not HEADING.fullmatch(heading) or '\r' in heading or '\n' in heading):
                raise ValueError('Unsupported ATX selector')
        if section['claim_kind'] not in ('observed', 'constraint', 'planned'):
            raise ValueError('Unknown claim kind')
        unique_list(section['watches'], path)
        unique_list(section['depends_on'], identifier)
        if not section['watches'] and not section['depends_on']:
            raise ValueError('Managed section has no verifiable basis')
    return document


def ordered_sections(links):
    sections = {s['id']: s for d in links['documents'] for s in d['sections']}
    if len(sections) != sum(len(d['sections']) for d in links['documents']):
        raise ValueError('Duplicate global section ID')
    if len(sections) > 512 or sum(len(s['depends_on']) for s in sections.values()) > 2048:
        raise ValueError('Graph limit exceeded')
    done = []
    remaining = set(sections)
    for section in sections.values():
        if set(section['depends_on']) - sections.keys(): raise ValueError('Unknown dependency')
    while remaining:
        ready = sorted(s for s in remaining if not (set(sections[s]['depends_on']) & remaining))
        if not ready: raise ValueError('Dependency cycle')
        # Choose one at a time for stable lexical topological ordering.
        current = ready[0]; done.append(current); remaining.remove(current)
    return done


def parse_links(data: bytes) -> dict[str, Any]:
    value = load_json(data, MAX_MAP)
    obj(value, {'schema', 'schema_version', 'project_id', 'documents', 'inventory_only'})
    if value['schema'] != 'agent-docs-kit/links' or type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('Unsupported links schema')
    try:
        if str(uuid.UUID(value['project_id'])) != value['project_id']: raise ValueError('Noncanonical UUID')
    except (ValueError, AttributeError, TypeError) as exc: raise ValueError('Invalid project UUID') from exc
    docs = value['documents']
    if not isinstance(docs, list) or not docs or len(docs) > 64: raise ValueError('Invalid document count')
    ids, origins, watched = set(), set(), set()
    for document in docs:
        validate_document(document)
        if document['id'] in ids or document['origin'] in origins: raise ValueError('Duplicate document')
        ids.add(document['id']); origins.add(document['origin'])
        watched.update(p for s in document['sections'] for p in s['watches'])
    ordered_sections(value)
    if not isinstance(value['inventory_only'], list): raise ValueError('Invalid inventory dispositions')
    disposed = set()
    for item in value['inventory_only']:
        obj(item, {'path', 'reason'}); p = path(item['path'])
        if p in watched or p in disposed or not isinstance(item['reason'], str) or not item['reason'].strip():
            raise ValueError('Invalid inventory disposition')
        disposed.add(p)
    return value


def section_spans(document: bytes, document_spec: dict[str, Any]) -> dict[str, Any]:
    validate_document(document_spec)
    if len(document) > MAX_MAP: raise ValueError('Markdown size limit exceeded')
    try: text = document.decode('utf-8')
    except UnicodeError as exc: raise ValueError('Markdown must be UTF-8') from exc
    if '\r' in text: raise ValueError('Markdown must use LF')
    headings = []
    fence = None
    offset = 0
    # Only physical LF terminates Markdown lines; Unicode separators remain text.
    for line in re.findall(r'[^\n]*\n|[^\n]+$', text):
        body = line.removesuffix('\n')
        if fence:
            if re.fullmatch(r' {0,3}' + re.escape(fence[0]) + '{' + str(fence[1]) + r',}[ \t]*', body): fence = None
        else:
            match = re.match(r' {0,3}(`{3,}|~{3,})(.*)\Z', body)
            if match and not (match[1][0] == '`' and '`' in match[2]):
                fence = (match[1][0], len(match[1]))
            else:
                match = HEADING.fullmatch(body)
                # H1 also terminates partial spans, but is not selectable.
                h1 = re.fullmatch(r' {0,3}#(?:[ \t]+.*)?', body)
                if match or h1: headings.append((offset, len(match[1]) if match else 1, body))
        offset += len(line.encode('utf-8'))
    if fence: raise ValueError('Unclosed fenced code block')
    spans = {}
    for section in document_spec['sections']:
        selector = section['selector']
        if selector['kind'] == 'document': spans[section['id']] = [0, len(document)]; continue
        found = [i for i, h in enumerate(headings) if h[2] == selector['heading']]
        if len(found) != 1: raise ValueError('Missing or ambiguous heading')
        index = found[0]; start, level, _ = headings[index]
        end = next((h[0] for h in headings[index + 1:] if h[1] <= level), len(document))
        spans[section['id']] = [start, end]
    ordered = sorted(spans.values())
    if any(a[1] > b[0] for a, b in zip(ordered, ordered[1:])): raise ValueError('Overlapping sections')
    selected = {s['selector'].get('heading') for s in document_spec['sections']}
    whole = document_spec['sections'][0]['selector']['kind'] == 'document'
    return {'spans': spans, 'unmapped_headings': [] if whole else [h[2] for h in headings if h[1] == 2 and h[2] not in selected],
            'preamble': [0, ordered[0][0]]}
