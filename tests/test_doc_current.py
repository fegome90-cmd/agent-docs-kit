import copy
import json
import unittest
import uuid
from knowledge_support import (make_repo, require_module, adopt_document, proposal_request,
    review_record, run_cli, git, inventory)


class CurrentTests(unittest.TestCase):
    def setUp(self):
        self.m=require_module(self,'doc_current');self.repo=make_repo(self);self.root=self.repo['root']
        import doc_revisions
        self.r=doc_revisions

    def accept(self,p):
        return self.r.accept_proposal(self.root,p['bundle'],review_record(self.repo,p),str(uuid.uuid4()),'review',confirm_reviewed=True)

    def read(self,**kwargs):return self.m.read_current(self.root,'links.json',**kwargs)

    def test_read_returns_unique_accepted_revision(self):
        adopted=adopt_document(self.repo)
        body=adopted['body'].replace(b'\nCatalog\n',b'\nFirst accepted\n')
        p=proposal_request(self.repo,mode='revise',body=body);accepted=self.accept(p)
        proposal_request(self.repo,mode='revise',body=body.replace(b'First accepted',b'Unaccepted draft'))
        before=inventory(self.root);result=self.read(document_id='project')
        self.assertEqual(result['body'],body.decode());self.assertEqual(result['selection'],'accepted')
        self.assertEqual(result['lineage']['event_id'],accepted['event']['event_id'])
        self.assertEqual(inventory(self.root),before)
        self.assertNotIn('body',self.read())
        self.assertEqual((self.root/'PROJECT.md').read_bytes(),adopted['body'])

    def test_read_blocks_stale_unaccepted_and_conflict(self):
        initial=self.read(document_id='project')
        self.assertEqual(initial['exit_code'],1)
        self.assertEqual(initial['origin'],'PROJECT.md')
        self.assertEqual(initial['map']['path'],'links.json')
        self.assertNotIn('body',self.read(document_id='project'))
        adopted=adopt_document(self.repo);old=(self.root/'src/catalog.py').read_bytes();(self.root/'src/catalog.py').write_bytes(b'new')
        result=self.read(document_id='project');self.assertEqual(result['state'],'stale');self.assertNotIn('body',result)
        (self.root/'src/catalog.py').write_bytes(old)
        p=proposal_request(self.repo,mode='revise',body=adopted['body'].replace(b'\nCatalog\n',b'\nA\n'))
        q=proposal_request(self.repo,mode='revise',body=adopted['body'].replace(b'\nPlan\n',b'\nB\n'))
        first=self.accept(p)['event'];segment=self.root/'docs/living/history'/ (first['session_id']+'.jsonl')
        saved=segment.read_bytes();segment.unlink();self.accept(q);segment.write_bytes(saved)
        result=self.read(document_id='project');self.assertEqual(result['selection'],'conflict');self.assertEqual(result['exit_code'],1);self.assertNotIn('body',result)
        self.assertEqual(len(result['conflict_revisions']),2)
        self.assertTrue(all(r['revision'].endswith('/document.md') for r in result['conflict_revisions']))

    def test_own_acceptance_does_not_make_revision_stale(self):
        adopted=adopt_document(self.repo)
        for word in (b'First revision',b'Second revision'):
            from doc_evidence import open_view
            context,_=self.r.resolved_context(open_view(self.root,'worktree'),self.repo['links'])
            base=context['base_documents']['project'];body=base.replace(b'\nCatalog\n',b'\n'+word+b'\n') if word.startswith(b'First') else base.replace(b'First revision',word)
            p=proposal_request(self.repo,mode='revise',body=body);self.accept(p)
            self.assertNotEqual(body,adopted['body'])
            result=self.read(document_id='project');self.assertEqual(result['freshness']['state'],'matches_review');self.assertEqual(result['body'],body.decode())
            from doc_impact import check_knowledge
            report=check_knowledge(self.root,'links.json','snapshot.json')
            self.assertEqual(report['review_queue'],[]);self.assertEqual(report['selection'],{'project':'accepted'})
            self.assertEqual(report['evidence']['base_documents'][0]['sha256'],self.r.digest(body))
            self.assertEqual(report['evidence']['base_documents'][0]['proposal_id'],p['manifest']['proposal_id'])

    def test_section_read_includes_dependencies_with_budget(self):
        adopt_document(self.repo);result=self.read(section_id='project.summary')
        self.assertEqual([s['section_id'] for s in result['sections']],['project.catalog','project.summary'])
        self.assertNotIn('project.runtime',[s['section_id'] for s in result['sections']])
        self.assertEqual(result['sections'][-1]['claim_kind'],'planned')
        rendered=self.m.render_current(result);self.assertIn('planned',rendered);self.assertLessEqual(len(rendered.encode()),32768)
        with self.assertRaises(ValueError):self.m.render_current(result,'unknown')

    def test_read_never_uses_clock_order(self):
        first=adopt_document(self.repo);p=proposal_request(self.repo,mode='revise',body=first['body'].replace(b'\nCatalog\n',b'\nNew\n'));self.accept(p)
        before=self.read(document_id='project')['body']
        for file in (self.root/'docs/living/history').glob('*.jsonl'):
            event=json.loads(file.read_bytes());event['occurred_at']='2000-01-01T00:00:00Z' if event['payload']['parents'] else '2040-01-01T00:00:00Z'
            file.write_bytes(self.r.canonical(event)+b'\n')
        self.assertEqual(self.read(document_id='project')['body'],before)

    def test_export_is_exclusive_and_proposal_label_preserved(self):
        adopt_document(self.repo)
        args=('knowledge','read',str(self.root),'--map','links.json','--section','project.summary','--output','current.md','--apply')
        first=run_cli(self.root,*args);self.assertEqual(first.returncode,0,first.stderr)
        raw=(self.root/'current.md').read_bytes();self.assertIn(b'planned',raw)
        second=run_cli(self.root,*args);self.assertEqual(second.returncode,1);self.assertEqual((self.root/'current.md').read_bytes(),raw)
        result=run_cli(self.root,'knowledge','read',str(self.root),'--map','links.json','--json')
        self.assertEqual(result.returncode,0,result.stderr);self.assertNotIn('body',json.loads(result.stdout))

    def test_map_revision_invalidates_old_selection_freshness(self):
        adopt_document(self.repo);new=copy.deepcopy(self.repo['links']);new['documents'][0]['sections'][0]['claim_kind']='planned'
        (self.root/'new-links.json').write_text(json.dumps(new))
        result=self.m.read_current(self.root,'new-links.json',document_id='project')
        self.assertEqual(result['state'],'stale');self.assertNotIn('body',result)
        self.assertIn('map_changed',result['freshness']['reasons'])

    def test_external_document_content_freshness_and_equal_reacceptance(self):
        links=copy.deepcopy(self.repo['links'])
        links['documents'].append({'id':'aux','origin':'AUX.md','sections':[{'id':'aux.body','selector':{'kind':'document'},
            'claim_kind':'observed','watches':['src/catalog.py'],'depends_on':[]}]})
        links['documents'][0]['sections'][1]['depends_on'].append('aux.body')
        (self.root/'links.json').write_text(json.dumps(links));(self.root/'AUX.md').write_bytes(b'# Auxiliary\nFirst\n');self.repo['links']=links
        aux=dict(self.repo,document_id='aux');adopt_document(aux);adopt_document(self.repo)
        section=self.read(section_id='project.summary')
        self.assertEqual({s['section_id'] for s in section['sections']},{'aux.body','project.catalog','project.summary'})
        equal=proposal_request(aux,mode='no_impact');self.accept(equal)
        self.assertEqual(self.read(document_id='project')['state'],'current')
        changed=proposal_request(aux,mode='revise',body=b'# Auxiliary\nSecond\n');self.accept(changed)
        result=self.read(document_id='project');self.assertEqual(result['state'],'stale');self.assertNotIn('body',result)
        self.assertIn('dependency_document_changed',result['freshness']['reasons'])
        self.assertEqual(self.read(document_id='aux')['state'],'current')

    def test_index_reader_never_uses_unstaged_acceptance_artifacts(self):
        original=(self.root/'src/catalog.py').read_bytes();(self.root/'src/catalog.py').write_bytes(b'local B')
        adopt_document(self.repo,view='index')
        self.assertEqual(self.read(view='index',document_id='project')['state'],'unaccepted')
        git(self.root,'add','docs/living/history')
        with self.assertRaises(ValueError):self.read(view='index',document_id='project')
        git(self.root,'add','docs')
        result=self.read(view='index',document_id='project');self.assertEqual(result['state'],'current')
        self.assertEqual(self.read(document_id='project')['state'],'stale')
        self.assertEqual((self.root/'src/catalog.py').read_bytes(),b'local B')

    def test_read_limits_fail_without_truncation(self):
        adopted=adopt_document(self.repo);body=adopted['body'].replace(b'\nCatalog\n',b'\n'+b'x'*33000+b'\n')
        p=proposal_request(self.repo,mode='revise',body=body);self.accept(p)
        with self.assertRaisesRegex(ValueError,'budget'):self.read(document_id='project')
        result=self.read(section_id='project.runtime');self.assertEqual(result['state'],'current')
        sections=[];document=b'# Many\n'
        for i in range(9):
            sections.append({'id':f'many.s{i}','selector':{'kind':'atx','heading':f'## S{i}'},'claim_kind':'observed',
                             'watches':['src/catalog.py'],'depends_on':[f'many.s{i-1}'] if i else []})
            document+=f'\n## S{i}\nBody\n'.encode()
        other=make_repo(self);other['links']['documents']=[{'id':'many','origin':'MANY.md','sections':sections}];other['document_id']='many'
        (other['root']/'MANY.md').write_bytes(document);(other['root']/'links.json').write_text(json.dumps(other['links']))
        adopt_document(other)
        with self.assertRaisesRegex(ValueError,'budget'):self.m.read_current(other['root'],'links.json',section_id='many.s8')
