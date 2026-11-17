"""Opt-in legacy gates use exactly the staged knowledge and preserve HEAD."""
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from knowledge_support import make_repo, adopt_document, proposal_request, review_record, git, run_cli, inventory
import git_gate
import living_docs


class KnowledgeGateTests(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo(self); self.root = self.repo['root']

    def gate(self, **kw):
        return git_gate.check_index(self.root, 'snapshot.json', knowledge_map='links.json', **kw)

    def adopted(self, commit=False):
        result = adopt_document(self.repo)
        self.assertEqual(git(self.root, 'add', '.').returncode, 0)
        if commit: self.assertEqual(git(self.root, 'commit', '-qm', 'adopt').returncode, 0)
        return result

    def test_legacy_commands_remain_compatible(self):
        before = living_docs.status(self.root, self.root/'snapshot.json')
        self.assertEqual(before, living_docs.status(self.root, self.root/'snapshot.json', knowledge_map=None))
        plain = git_gate.check_index(self.root, 'snapshot.json')
        self.assertEqual(plain, git_gate.check_index(self.root, 'snapshot.json', knowledge_map=None))
        self.assertNotIn('knowledge', plain)
        self.assertEqual(self.gate()['state'], 'review_required')
        cli = run_cli(self.root, 'status', str(self.root), '--snapshot', 'snapshot.json', '--knowledge-map', 'links.json', '--json')
        self.assertEqual(cli.returncode, 1, cli.stderr)
        self.assertIn('knowledge', json.loads(cli.stdout))

    def test_index_recomputes_review_obligations(self):
        self.adopted()
        self.assertEqual(self.gate()['knowledge']['review_queue'], [])
        (self.root/'src/catalog.py').write_bytes(b'changed\n'); git(self.root, 'add', 'src/catalog.py')
        (self.root/'src/catalog.py').write_bytes(b'catalog = "a b"\n')
        self.assertEqual(self.gate()['knowledge']['review_queue'], ['project.catalog','project.summary'])

    def test_gate_requires_staged_map_bundle_and_events(self):
        adopted = self.adopted()
        for name in ('links.json', adopted['bundle']+'/document.md', adopted['bundle']+'/evidence.json', adopted['bundle']+'/proposal.json', 'snapshot.json'):
            with self.subTest(name=name):
                git(self.root,'rm','--cached','-f',name)
                with self.assertRaises(ValueError): self.gate()
                git(self.root,'add',name)
        git(self.root,'rm','--cached','-r','docs/living/history')
        self.assertEqual(self.gate()['knowledge']['selection'], {'project':'unaccepted'})

    def test_gate_detects_revision_deletion_or_rewrite(self):
        adopted = self.adopted(commit=True)
        paths = [adopted['bundle']+'/'+n for n in ('document.md','evidence.json','proposal.json')]
        paths += ['links.json','snapshot.json',adopted['result']['event']['payload']['review']['path']]
        for name in paths:
            with self.subTest(name=name):
                original=(self.root/name).read_bytes(); (self.root/name).write_bytes(original+b' ');git(self.root,'add',name)
                with self.assertRaises(ValueError): self.gate()
                (self.root/name).write_bytes(original);git(self.root,'add',name)

    def test_integration_cannot_silence_previous_failure(self):
        self.adopted();(self.root/'src/catalog.py').write_bytes(b'changed');git(self.root,'add','src/catalog.py')
        with patch('doc_impact.check_knowledge',return_value={'state':'no_linked_change'}):
            self.assertEqual(self.gate()['state'],'review_required')

    def test_gate_does_not_write_or_install_hooks(self):
        self.adopted(); before=inventory(self.root); self.gate();self.assertEqual(inventory(self.root),before)
        alternate=self.root/'alternate-index';alternate.write_bytes((self.root/'.git/index').read_bytes())
        before=inventory(self.root)
        with patch.dict(os.environ,{'GIT_INDEX_FILE':str(alternate)}): self.assertEqual(self.gate()['state'],'clean')
        self.assertEqual(inventory(self.root),before)

    def test_unknown_knowledge_event_version_fails_closed(self):
        adopted=self.adopted(); event=adopted['result']['event']; name='docs/living/history/'+event['session_id']+'.jsonl'
        event['payload']['schema_version']=999;(self.root/name).write_text(json.dumps(event)+'\n');git(self.root,'add',name)
        with self.assertRaises(ValueError): self.gate()

    def test_status_cwd_relative_and_external_snapshots(self):
        self.adopted()
        result=run_cli(self.root/'src','status',str(self.root),'--snapshot','../snapshot.json','--knowledge-map','links.json','--json')
        self.assertEqual(result.returncode,0,result.stderr)
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            external=Path(tmp).resolve()/'snapshot.json';external.write_bytes((self.root/'snapshot.json').read_bytes())
            plain=run_cli(self.root,'status',str(self.root),'--snapshot',str(external),'--json')
            self.assertEqual(plain.returncode,0,plain.stderr)
            opt=run_cli(self.root,'status',str(self.root),'--snapshot',str(external),'--knowledge-map','links.json','--json')
            self.assertEqual(opt.returncode,3);self.assertIn(b'unsupported_external_snapshot',opt.stdout+opt.stderr)

    def test_history_deletion_parent_rewrite_and_truncation(self):
        adopted=self.adopted(commit=True)
        import uuid
        from doc_revisions import accept_proposal
        p=proposal_request(self.repo,mode='no_impact'); accepted=accept_proposal(self.root,p['bundle'],review_record(self.repo,p),str(uuid.uuid4()),'second',confirm_reviewed=True)
        git(self.root,'add','.');git(self.root,'commit','-qm','second')
        name='docs/living/history/'+accepted['event']['session_id']+'.jsonl';original=(self.root/name).read_bytes()
        for raw in (b'', original[:-4], original.replace(adopted['result']['event']['event_id'].encode(),str(uuid.uuid4()).encode())):
            (self.root/name).write_bytes(raw);git(self.root,'add',name)
            try: self.assertEqual(self.gate()['state'],'review_required')
            except ValueError: pass
        (self.root/name).write_bytes(original);git(self.root,'add',name)
        self.assertEqual(self.gate()['state'],'clean')

    def test_history_argument_is_used(self):
        self.adopted()
        self.assertEqual(self.gate(history_path='other/history')['knowledge']['selection'],{'project':'unaccepted'})
