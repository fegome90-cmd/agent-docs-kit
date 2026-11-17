import copy
import json
import unittest
from knowledge_support import require_module, link_map


class LinksTests(unittest.TestCase):
    def setUp(self):
        self.m = require_module(self, 'doc_links')
        self.links = link_map()

    def parse(self, value):
        return self.m.parse_links(json.dumps(value).encode())

    def test_map_rejects_duplicate_ids(self):
        self.assertEqual(self.parse(self.links), self.links)
        self.links['documents'][0]['sections'][1]['id'] = 'project.catalog'
        with self.assertRaises(ValueError): self.parse(self.links)

    def test_map_rejects_unknown_dependency_and_cycle(self):
        for dep in ['absent', 'project.summary']:
            with self.subTest(dep=dep):
                value = copy.deepcopy(self.links)
                value['documents'][0]['sections'][0]['depends_on'] = [dep]
                with self.assertRaises(ValueError): self.parse(value)

    def test_heading_inside_code_is_not_an_anchor(self):
        doc = self.links['documents'][0]
        doc['sections'] = doc['sections'][:1]
        for fence in [b'```python', b'~~~']:
            raw = b'# P\n' + fence + b'\n## Catalog\n' + fence[:3] + b'\n## Catalog\nreal\n'
            result = self.m.section_spans(raw, doc)
            a, b = result['spans']['project.catalog']
            self.assertEqual(raw[a:b], b'## Catalog\nreal\n')
        with self.assertRaises(ValueError):
            self.m.section_spans(b'```\n## Catalog\n```\n', doc)

    def test_duplicate_or_missing_heading_fails(self):
        for raw in [b'## Catalog\nx\n## Catalog\ny\n', b'## Different\n']:
            with self.assertRaises(ValueError): self.m.section_spans(raw, self.links['documents'][0])

    def test_section_spans_preserve_raw_bytes(self):
        doc = self.links['documents'][0]
        doc['sections'] = doc['sections'][:1]
        raw = '# Pré\n\n## Catalog\n| á | b |\n\n### Detail\nx\n## Other\nend\n'.encode()
        result = self.m.section_spans(raw, doc)
        a, b = result['spans']['project.catalog']
        self.assertEqual(raw[a:b], '## Catalog\n| á | b |\n\n### Detail\nx\n'.encode())
        self.assertEqual(raw[:a], '# Pré\n\n'.encode())
        self.assertEqual(raw[:a] + raw[a:b] + raw[b:], raw)
        self.assertEqual(result['preamble'], [0, a])

    def test_unmanaged_section_is_not_approved(self):
        doc = self.links['documents'][0]
        doc['sections'] = doc['sections'][:1]
        result = self.m.section_spans(b'## Catalog\na\n## Unknown\nb\n', doc)
        self.assertEqual(result['unmapped_headings'], ['## Unknown'])

    def test_inventory_only_requires_reason(self):
        for entry in [{'path': 'src/other', 'reason': ''},
                      {'path': 'src/catalog.py', 'reason': 'inventory'}]:
            self.links['inventory_only'] = [entry]
            with self.assertRaises(ValueError): self.parse(self.links)
        self.links['inventory_only'] = [{'path': 'src/other', 'reason': 'inventory'}]
        self.assertEqual(self.parse(self.links)['inventory_only'], self.links['inventory_only'])

    def test_overlapping_spans_and_unsupported_structure_fail(self):
        doc = self.links['documents'][0]
        doc['sections'] = doc['sections'][:1]
        with self.assertRaises(ValueError): self.m.section_spans(b'## Catalog\n```\n', doc)
        doc['sections'].append(dict(doc['sections'][0], id='whole', selector={'kind': 'document'}))
        with self.assertRaises(ValueError): self.m.section_spans(b'## Catalog\n', doc)
        doc['sections'] = doc['sections'][1:]
        self.assertEqual(self.m.section_spans(b'text\n', doc)['spans'], {'whole': [0, 5]})

    def test_strict_nested_schemas_and_limits(self):
        for mutate in [lambda x: x.update(extra=True),
                       lambda x: x['documents'][0].update(extra=True),
                       lambda x: x['documents'][0]['sections'][0]['selector'].update(extra=True),
                       lambda x: x.update(schema_version=True),
                       lambda x: x['documents'][0]['sections'][0].update(watches=['../bad']),
                       lambda x: x['documents'][0]['sections'][0].update(watches=[]),
                       lambda x: x['documents'][0]['sections'][0].update(claim_kind='fact')]:
            value = copy.deepcopy(self.links); mutate(value)
            with self.assertRaises(ValueError): self.parse(value)
        for raw in [b'{}' + b' ' * 1048576, b'{"x":1,"x":2}', b'[' * 65 + b'0' + b']' * 65]:
            with self.assertRaises(ValueError): self.m.parse_links(raw)
        with self.assertRaises(ValueError): self.m.section_spans(b'## Catalog\r\n', self.links['documents'][0])
