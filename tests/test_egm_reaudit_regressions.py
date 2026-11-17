"""Adversarial regressions from completed native Goldfish round 1."""
import builtins
import contextlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid
from knowledge_support import make_repo, adopt_document, proposal_request, review_record, git, inventory, KIT


class GoldfishReauditTests(unittest.TestCase):
    def setUp(self):
        self.repo=make_repo(self); self.root=self.repo['root']

    def test_prose_edit_requires_same_document_consumer_review(self):
        adopted=adopt_document(self.repo)
        body=adopted['body'].replace(b'\nCatalog\n',b'\nChanged explanation\n')
        with self.assertRaisesRegex(ValueError,'review|Review'):
            proposal_request(self.repo,mode='revise',body=body,reviewed=['project.catalog'])
        # A review of the dependent need not rewrite its unchanged prose.
        valid=proposal_request(self.repo,mode='revise',body=body,reviewed=['project.catalog','project.summary'])
        self.assertEqual(valid['body'],body)
        self.assertNotIn('project.runtime',valid['manifest']['reviewed_sections'])

    def test_accept_rechecks_candidate_prose_dependency_closure(self):
        from doc_revisions import accept_proposal, load_proposal, proposal_id
        from doc_evidence import open_view
        from doc_links import canonical
        adopted=adopt_document(self.repo)
        body=adopted['body'].replace(b'\nCatalog\n',b'\nChanged explanation\n')
        q=proposal_request(self.repo,mode='revise',body=body,reviewed=['project.catalog','project.summary'])
        bundle=load_proposal(open_view(self.root,'worktree'),q['bundle'])
        # Untrusted, internally consistent proposal input: integrity alone must
        # not replace semantic-scope validation on the acceptance path.
        manifest=bundle['manifest'];manifest['reviewed_sections']=['project.catalog']
        manifest['proposal_id']=proposal_id(manifest,bundle['evidence'])
        (self.root/q['bundle']/'proposal.json').write_bytes(canonical(manifest)+b'\n')
        q['manifest']=manifest;review=review_record(self.repo,q)
        before=inventory(self.root/'docs/living/history')
        with self.assertRaisesRegex(ValueError,'review|Review'):
            accept_proposal(self.root,q['bundle'],review,str(uuid.uuid4()),'incomplete',confirm_reviewed=True)
        self.assertEqual(inventory(self.root/'docs/living/history'),before)

    def test_transitive_candidate_prose_dependents_are_reviewed(self):
        links=self.repo['links']
        links['documents'][0]['sections'].append({'id':'project.overview','selector':{'kind':'atx','heading':'## Overview'},'claim_kind':'observed','watches':[],'depends_on':['project.summary']})
        (self.root/'PROJECT.md').write_bytes((self.root/'PROJECT.md').read_bytes()+b'\n## Overview\nOverview\n')
        (self.root/'links.json').write_text(json.dumps(links))
        adopted=adopt_document(self.repo)
        body=adopted['body'].replace(b'\nCatalog\n',b'\nChanged explanation\n')
        with self.assertRaisesRegex(ValueError,'review|Review'):
            proposal_request(self.repo,mode='revise',body=body,reviewed=['project.catalog','project.summary'])

    def test_index_gate_size_bound_precedes_source_blob_request(self):
        import git_gate,doc_evidence
        (self.root/'src/catalog.py').write_bytes(b'x'*256)
        self.assertEqual(git(self.root,'add','src/catalog.py').returncode,0)
        oid=git(self.root,'rev-parse',':src/catalog.py').stdout.decode().strip()
        calls=[];original=git_gate._Git.run
        def spy(instance,*args,**kwargs):
            if args==('cat-file','blob',oid):calls.append(args)
            return original(instance,*args,**kwargs)
        with patch.object(doc_evidence,'MAX_SOURCE_BYTES',32),patch.object(git_gate._Git,'run',spy):
            with self.assertRaises(ValueError):
                git_gate.check_index(self.root,'snapshot.json',knowledge_map='links.json')
        self.assertEqual(calls,[],'Oversized source body was materialized before rejection')
        # Opt-in bound must not change the unflagged v0.4 contract.
        legacy=git_gate.check_index(self.root,'snapshot.json')
        self.assertEqual(legacy['state'],'review_required')

    def test_index_gate_bounds_snapshot_before_json_allocation(self):
        import git_gate,doc_evidence
        oid=git(self.root,'rev-parse',':snapshot.json').stdout.decode().strip()
        calls=[];original=git_gate._Git.run
        def spy(instance,*args,**kwargs):
            if args==('cat-file','blob',oid):calls.append(args)
            return original(instance,*args,**kwargs)
        with patch.object(doc_evidence,'MAX_EVIDENCE',32),patch.object(git_gate._Git,'run',spy):
            with self.assertRaises(ValueError):
                git_gate.check_index(self.root,'snapshot.json',knowledge_map='links.json')
        self.assertEqual(calls,[],'Oversized snapshot was read before knowledge cap')

    def test_missing_portalocker_is_operational_before_storage_creation(self):
        import agent_docs_kit
        from doc_impact import check_knowledge
        report=check_knowledge(self.root,'links.json','snapshot.json')
        request={'schema':'agent-docs-kit/proposal-request','schema_version':1,'request_id':str(uuid.uuid4()),
                 'project_id':self.repo['links']['project_id'],'document_id':'project','mode':'bootstrap',
                 'candidate_path':'PROJECT.md','expected_parents':[],
                 'reviewed_sections':sorted(s['id'] for s in self.repo['links']['documents'][0]['sections']),
                 'reason':'Synthetic review','evidence':report['evidence']}
        (self.root/'request.json').write_text(json.dumps(request))
        original=builtins.__import__
        def missing(name,*args,**kwargs):
            if name=='portalocker':raise ImportError('portalocker intentionally absent')
            return original(name,*args,**kwargs)
        with patch('builtins.__import__',side_effect=missing),contextlib.redirect_stdout(io.StringIO()) as output:
            try:
                code=agent_docs_kit.main(['knowledge','propose',str(self.root),'--request','request.json','--output','bundle','--apply','--json'])
            except ImportError:
                self.fail('Untranslated dependency ImportError escaped the CLI')
        self.assertEqual(code,3)
        self.assertEqual(json.loads(output.getvalue())['state'],'error')
        self.assertFalse((self.root/'docs/living/history').exists())
        self.assertFalse((self.root/'bundle').exists())

    def test_history_entry_budget_stops_enumeration_early(self):
        from doc_evidence import open_view
        view=open_view(self.root,'worktree')
        (self.root/'docs/living/history').mkdir(parents=True)
        count=[0]
        class Entries:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def __iter__(self):
                for i in range(20000):
                    count[0]+=1
                    if count[0]>16385:raise AssertionError('Unbounded enumeration past entry budget')
                    yield SimpleNamespace(name=str(i))
        with patch('doc_evidence.os.scandir',return_value=Entries()):
            with self.assertRaisesRegex(ValueError,'entry budget'):
                view.history_paths('docs/living/history')
        self.assertEqual(count[0],16385)

    def pilot_module(self):
        location=KIT/'validation/pilot_knowledge.py'
        spec=importlib.util.spec_from_file_location('pilot_reaudit',location)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertTrue(hasattr(module,'compare_native_tests'),'Missing observed-baseline comparison')
        return module

    def test_pilot_accepts_improved_or_changed_before_baseline(self):
        module=self.pilot_module()
        clean={'exit':0,'failures':[]}
        result=module.compare_native_tests(clean,clean)
        self.assertEqual(result['state'],'unchanged')
        self.assertEqual(set(result['resolved_historical']),set(module.EXPECTED_FAILURES))
        changed={'exit':1,'failures':['preexisting different test']}
        result=module.compare_native_tests(changed,changed)
        self.assertEqual(result['state'],'unchanged')
        self.assertEqual(result['unexpected_baseline'],changed['failures'])
        self.assertEqual(module.compare_native_tests(changed,clean)['state'],'improved')

    def test_pilot_never_swallows_new_failures_or_runner_errors(self):
        module=self.pilot_module()
        clean={'exit':0,'failures':[]}
        result=module.compare_native_tests(clean,{'exit':1,'failures':['new failure']})
        self.assertEqual(result['state'],'regressed')
        self.assertEqual(result['introduced'],['new failure'])
        for invalid in ({'exit':124,'failures':[]},{'exit':1,'failures':[]},{'exit':0,'failures':['contradiction']}):
            with self.subTest(invalid=invalid),self.assertRaises(RuntimeError):
                module.compare_native_tests(invalid,clean)

    def test_index_gate_bounds_document_before_blob_allocation(self):
        import git_gate,doc_revisions,doc_current
        oid=git(self.root,'rev-parse',':PROJECT.md').stdout.decode().strip()
        calls=[];original=git_gate._Git.run
        def spy(instance,*args,**kwargs):
            if args==('cat-file','blob',oid):calls.append(args)
            return original(instance,*args,**kwargs)
        with patch.object(doc_revisions,'MAX_DOCUMENT',32),patch.object(git_gate._Git,'run',spy):
            with self.assertRaises(ValueError):
                git_gate.check_index(self.root,'snapshot.json',knowledge_map='links.json')
        self.assertEqual(calls,[],'Oversized document was read before document cap')
