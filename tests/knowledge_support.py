"""Deterministic local-only fixtures for knowledge contracts."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))


def require_module(case, name):
    case.assertIsNotNone(importlib.util.find_spec(name), f"Missing capability: {name}")
    return importlib.import_module(name)


def git(root, *args, env=None):
    environment = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_TERMINAL_PROMPT='0')
    environment.update(env or {})
    return subprocess.run(['git', '-c', f'core.hooksPath={os.devnull}', '-C', str(root), *args],
                          env=environment, capture_output=True, timeout=30)


def run_cli(root, *args, env=None):
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    environment.update(env or {})
    return subprocess.run([sys.executable, '-B', str(KIT / 'agent_docs_kit.py'), *args],
                          cwd=root, env=environment, capture_output=True, timeout=30)


def inventory(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def link_map():
    return {'schema': 'agent-docs-kit/links', 'schema_version': 1,
            'project_id': '12345678-1234-4234-8234-123456789abc',
            'documents': [{'id': 'project', 'origin': 'PROJECT.md', 'sections': [
                {'id': 'project.catalog', 'selector': {'kind': 'atx', 'heading': '## Catalog'},
                 'claim_kind': 'observed', 'watches': ['src/catalog.py'], 'depends_on': []},
                {'id': 'project.summary', 'selector': {'kind': 'atx', 'heading': '## Summary'},
                 'claim_kind': 'planned', 'watches': [], 'depends_on': ['project.catalog']},
                {'id': 'project.runtime', 'selector': {'kind': 'atx', 'heading': '## Runtime'},
                 'claim_kind': 'constraint', 'watches': ['src/runtime.py'], 'depends_on': []}]}],
            'inventory_only': []}


def make_repo(case):
    temp = tempfile.TemporaryDirectory()
    case.addCleanup(temp.cleanup)
    root = Path(temp.name).resolve()
    case.assertEqual(git(root, 'init', '-q').returncode, 0)
    for key, value in [('user.name', 'Synthetic'), ('user.email', 'synthetic@example.invalid')]:
        case.assertEqual(git(root, 'config', key, value).returncode, 0)
    (root / 'src').mkdir()
    (root / 'src/catalog.py').write_bytes(b'catalog = "a b"\n')
    (root / 'src/runtime.py').write_bytes(b'runtime = True\n')
    (root / 'PROJECT.md').write_bytes(b'# Project\n\n## Catalog\nCatalog\n\n## Summary\nPlan\n\n## Runtime\nRule\n')
    links = link_map()
    (root / 'links.json').write_text(json.dumps(links), encoding='utf-8')
    from living_docs import snapshot
    snapshot(root, ['src'], ['PROJECT.md'], root / 'snapshot.json', discover_roots=['src'])
    case.assertEqual(git(root, 'add', '.').returncode, 0)
    case.assertEqual(git(root, 'commit', '-qm', 'fixture').returncode, 0)
    return {'root': root, 'links': links}


def proposal_request(repo, *, mode='bootstrap', body=None, reviewed=None, view='worktree', reason='Synthetic review', output=None):
    import uuid
    from doc_evidence import open_view, capture_evidence
    from doc_links import parse_links
    from doc_revisions import resolved_context, create_proposal
    root=repo['root']; reader=open_view(root,view)
    links=parse_links(reader.read('links.json',maximum=1048576))
    context,_=resolved_context(reader,links)
    evidence=capture_evidence(reader,links,'snapshot.json','links.json',context['base_documents'],context['heads'],proposal_ids=context['proposal_ids'])
    request_id=str(uuid.uuid4()); candidate='candidate-'+request_id+'.md'; request_path='request-'+request_id+'.json'
    did=repo.get('document_id','project');spec=next(d for d in links['documents'] if d['id']==did)
    (root/candidate).write_bytes(context['base_documents'][did] if body is None else body)
    request={'schema':'agent-docs-kit/proposal-request','schema_version':1,'request_id':request_id,
             'project_id':links['project_id'],'document_id':did,'mode':mode,'candidate_path':candidate,
             'expected_parents':context['heads'][did], 'reviewed_sections': sorted(s['id'] for s in spec['sections']) if reviewed is None else sorted(reviewed),
             'reason':reason,'evidence':evidence}
    (root/request_path).write_text(json.dumps(request)); bundle=output or 'docs/living/revisions/'+request_id
    manifest=create_proposal(root,request_path,bundle)
    return {'manifest':manifest,'bundle':bundle,'request':request,'request_path':request_path,'body':(root/candidate).read_bytes()}


def review_record(repo, proposal, *, authorizations=None, name=None):
    import uuid
    review={'schema':'agent-docs-kit/review','schema_version':1,'proposal_id':proposal['manifest']['proposal_id'],
            'decision':'accept','reviewer':{'kind':'agent','id':'synthetic-reviewer'},'reason':'Synthetic fixture review',
            'review_reference':'fixture://review','constraint_authorizations':authorizations or []}
    name=name or 'review-'+str(uuid.uuid4())+'.json'
    (repo['root']/name).write_text(json.dumps(review));return name


def adopt_document(repo, *, view='worktree'):
    import uuid
    from doc_revisions import accept_proposal
    proposal=proposal_request(repo,view=view)
    review=review_record(repo,proposal)
    result=accept_proposal(repo['root'],proposal['bundle'],review,str(uuid.uuid4()),'adopt',confirm_reviewed=True)
    return dict(proposal,result=result,review=review)
