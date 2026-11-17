import copy
import json
import unittest
import uuid
from unittest.mock import patch
from knowledge_support import make_repo, require_module, inventory, run_cli


class ProposalTests(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo(self); self.root = self.repo['root']
        self.m = require_module(self, 'doc_revisions')

    def request(self, *, mode='bootstrap', body=None, reviewed=None, reason='Synthetic review'):
        from doc_impact import check_knowledge
        evidence = check_knowledge(self.root, 'links.json', 'snapshot.json')['evidence']
        raw = (self.root / 'PROJECT.md').read_bytes() if body is None else body
        (self.root / 'candidate.md').write_bytes(raw)
        request = {'schema':'agent-docs-kit/proposal-request','schema_version':1,
            'request_id':str(uuid.uuid4()),'project_id':self.repo['links']['project_id'],
            'document_id':'project','mode':mode,'candidate_path':'candidate.md','expected_parents':[],
            'reviewed_sections':reviewed if reviewed is not None else sorted(s['id'] for s in self.repo['links']['documents'][0]['sections']),
            'reason':reason,'evidence':evidence}
        (self.root / 'request.json').write_text(json.dumps(request))
        return request

    def test_propose_creates_new_complete_bundle(self):
        request = self.request(); original = (self.root / 'PROJECT.md').read_bytes()
        result = self.m.create_proposal(self.root, 'request.json', 'bundle')
        from doc_evidence import open_view
        loaded = self.m.load_proposal(open_view(self.root,'worktree'), 'bundle')
        self.assertEqual(loaded['manifest']['proposal_id'],result['proposal_id'])
        self.assertEqual(loaded['document'],original)
        self.assertEqual((self.root / 'PROJECT.md').read_bytes(),original)
        self.assertEqual(set(p.name for p in (self.root/'bundle').iterdir()), {'document.md','evidence.json','proposal.json'})
        result = run_cli(self.root,'knowledge','propose',str(self.root),'--request','request.json','--output','other')
        self.assertEqual(result.returncode,2,result.stderr); self.assertFalse((self.root/'other').exists())

    def test_outside_section_change_is_rejected(self):
        raw=(self.root/'PROJECT.md').read_bytes()
        spec=self.repo['links']['documents'][0]
        with self.assertRaisesRegex(ValueError,'scope'):
            self.m.validate_edit(raw,raw.replace(b'Rule',b'Changed rule'),spec,['project.catalog'])
        self.m.validate_edit(raw,raw.replace(b'\nCatalog\n',b'\nUpdated\n'),spec,['project.catalog'])
        for bad in (raw.replace(b'## Catalog',b'## Other'), raw.replace(b'# Project',b'# Other')):
            with self.assertRaises(ValueError): self.m.validate_edit(raw,bad,spec,['project.catalog'])

    def test_no_impact_requires_identical_body_and_reason(self):
        raw=(self.root/'PROJECT.md').read_bytes()
        for body,reason in ((raw+b'x','reason'),(raw,'')):
            self.request(mode='no_impact',body=body,reason=reason)
            with self.assertRaises(ValueError): self.m.create_proposal(self.root,'request.json','bad')
            self.assertFalse((self.root/'bad').exists())
        self.request(mode='no_impact'); self.m.create_proposal(self.root,'request.json','good')

    def test_bootstrap_does_not_rewrite_origin(self):
        self.request(body=b'# Altered\n')
        with self.assertRaises(ValueError):self.m.create_proposal(self.root,'request.json','bundle')
        self.assertFalse((self.root/'bundle').exists())

    def test_complete_retry_and_incomplete_bundle(self):
        request=self.request(); first=self.m.create_proposal(self.root,'request.json','bundle'); before=inventory(self.root); evidence_bytes=(self.root/'bundle/evidence.json').read_bytes()
        request['evidence']['observed_at']='2030-01-01T00:00:00.123456Z'
        (self.root/'request.json').write_text(json.dumps(request))
        self.assertEqual(self.m.create_proposal(self.root,'request.json','bundle')['proposal_id'], first['proposal_id'])
        self.assertEqual((self.root/'bundle/evidence.json').read_bytes(),evidence_bytes)
        request['reason']='different'; (self.root/'request.json').write_text(json.dumps(request))
        with self.assertRaises(FileExistsError):self.m.create_proposal(self.root,'request.json','bundle')
        for stop in (1,2,3):
            self.request(); original=self.m.write_new; calls=[]
            def fail(target,raw):
                calls.append(target.name)
                if len(calls)==stop:raise OSError('injected')
                return original(target,raw)
            with patch.object(self.m,'write_new',side_effect=fail):
                with self.assertRaises(OSError): self.m.create_proposal(self.root,'request.json',f'partial{stop}')
            frozen=inventory(self.root)
            with self.assertRaises((ValueError,FileExistsError)):self.m.create_proposal(self.root,'request.json',f'partial{stop}')
            self.assertEqual(inventory(self.root),frozen)

    def test_preproposal_evidence_expired(self):
        for filename in ('src/catalog.py','PROJECT.md','links.json'):
            old=(self.root/filename).read_bytes();self.request()
            (self.root/filename).write_bytes(old+b' ')
            with self.assertRaises(ValueError):self.m.create_proposal(self.root,'request.json','bundle')
            self.assertFalse((self.root/'bundle').exists());(self.root/filename).write_bytes(old)

    def test_proposal_does_not_publish(self):
        self.request();self.m.create_proposal(self.root,'request.json','bundle')
        self.assertFalse(list((self.root/'docs/living/history').glob('*.jsonl')))
        from doc_impact import check_knowledge
        self.assertEqual(check_knowledge(self.root,'links.json','snapshot.json')['selection'],{'project':'unaccepted'})

    def test_strict_request_and_bundle_schemas(self):
        request=self.request();request['extra']=1;(self.root/'request.json').write_text(json.dumps(request))
        with self.assertRaises(ValueError):self.m.create_proposal(self.root,'request.json','bundle')
        self.request(); self.m.create_proposal(self.root,'request.json','bundle')
        from doc_evidence import open_view
        p=self.root/'bundle/document.md';p.write_bytes(p.read_bytes()+b'corrupt')
        with self.assertRaises(ValueError):self.m.load_proposal(open_view(self.root,'worktree'),'bundle')

    def test_partial_review_cannot_refresh_other_stale_sections(self):
        from doc_evidence import open_view
        request=self.request(); old=request['evidence']
        (self.root/'src/catalog.py').write_bytes(b'changed catalog\n')
        request=self.request(mode='revise',reviewed=['project.catalog'])
        reader=open_view(self.root,'worktree')
        context,_=self.m.resolved_context(reader,self.repo['links'])
        with self.assertRaisesRegex(ValueError,'Incomplete section review'):
            self.m.validate_candidate(reader,request,(self.root/'candidate.md').read_bytes(),context,
                                      {'project':{'evidence':old,'document':context['base_documents']['project']}})
        self.assertFalse((self.root/'bundle').exists())
