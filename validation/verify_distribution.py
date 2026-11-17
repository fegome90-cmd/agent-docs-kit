#!/usr/bin/env python3
"""Verify ordered/combined patches, archive identity, and a full fresh-ZIP suite.

Standalone validator. Unit tests must never invoke this validator recursively.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
import subprocess
import time
import sys
import zipfile

EXCLUDE={'.git','.venv','__pycache__'}


def inventory(root: Path) -> dict:
    out={}
    for directory,dirs,names in os.walk(root,followlinks=False):
        for name in list(dirs):
            if (Path(directory)/name).is_symlink():raise ValueError('Symlink directory in kit')
            if name in EXCLUDE:dirs.remove(name)
        for name in names:
            p=Path(directory)/name
            if p.is_symlink() or not p.is_file():raise ValueError('Nonregular kit member')
            if name.endswith(('.pyc','.pyo')):continue
            out[p.relative_to(root).as_posix()]={'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
                                               'executable':bool(p.stat().st_mode&0o111)}
    return dict(sorted(out.items()))


def copy(source: Path,target: Path) -> None:
    target.mkdir(exist_ok=False)
    for relative in inventory(source):
        dest=target/relative;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source/relative,dest)


def verify_manifest(root: Path) -> int:
    manifest=json.loads((root/'MANIFEST.sha256.json').read_text(encoding='utf-8'))
    actual=inventory(root)
    if set(actual)-{'MANIFEST.sha256.json'}!=set(manifest):raise ValueError('Manifest coverage mismatch')
    for p,h in manifest.items():
        if actual[p]['sha256']!=h:raise ValueError('Manifest hash mismatch: '+p)
    return len(manifest)


def extract(archive: Path,parent: Path,prefix: str) -> Path:
    parent.mkdir(exist_ok=False);seen=set()
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            name=info.filename;path=PurePosixPath(name)
            if (path.is_absolute() or PureWindowsPath(name).drive or '..' in path.parts
                    or path.as_posix() != name.rstrip('/') or '\\' in name or name in seen):
                raise ValueError('Unsafe or duplicate archive path')
            seen.add(name)
            if not name.startswith(prefix+'/'):raise ValueError('Unexpected archive root')
            if info.is_dir():continue
            mode=info.external_attr>>16
            if stat.S_IFMT(mode) not in (0,stat.S_IFREG):raise ValueError('Nonregular archive member')
            dest=parent/name;dest.parent.mkdir(parents=True,exist_ok=True)
            with dest.open('xb') as f:f.write(z.read(info))
            os.chmod(dest,0o755 if mode&0o111 else 0o644)
    return parent/prefix


def validate_roots(base, candidate, patches, output):
    """Reject overlap before creating any output or copying a source tree."""
    roots = tuple(Path(p).resolve() for p in (base, candidate, patches, output))
    for p in roots[:3]:
        if not p.is_dir():
            raise ValueError('Input root must be an existing directory')
    for i, left in enumerate(roots):
        for right in roots[i + 1:]:
            if left.is_relative_to(right) or right.is_relative_to(left):
                raise ValueError('Validation roots must be disjoint')
    if roots[-1].exists():
        raise ValueError('Output must be a new directory')
    return roots


def verify(base: Path,candidate: Path,patches: Path,output: Path,python: Path,graphify: bool,timeout: int) -> dict:
    base,candidate,patches,output = validate_roots(base,candidate,patches,output)
    output.mkdir(parents=True,exist_ok=False)
    initial_base=inventory(base);expected=inventory(candidate)
    count=verify_manifest(candidate)
    series=json.loads((patches/'series.json').read_text(encoding='utf-8'))
    version=series['version']
    if not isinstance(version,str) or not re.fullmatch(r'[0-9A-Za-z][0-9A-Za-z.+-]*',version):raise ValueError('Unsafe release version')
    names=[item['patch'] for item in series['patches']]
    if len(names)!=8 or len(set(names))!=8:raise ValueError('Expected eight ordered patches')
    env=dict({k:v for k,v in os.environ.items() if not k.startswith('GIT_')},GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,GIT_OPTIONAL_LOCKS='0',GIT_TERMINAL_PROMPT='0',PYTHONDONTWRITEBYTECODE='1')
    env['PATH']=str(python.parent)+os.pathsep+env.get('PATH','')
    env['KIT_E2E_GRAPHIFY']='1' if graphify else '0'
    def apply(tree: Path,patchfile: Path):
        for args in [('apply','--check'),('apply',)]:
            p=subprocess.run(['git','-c','core.hooksPath='+os.devnull,*args,str(patchfile.resolve())],cwd=tree,env=env,capture_output=True,timeout=60)
            if p.returncode:raise RuntimeError(p.stderr.decode(errors='replace'))
    applied=output/'applied-series';copy(base,applied)
    for item in series['patches']:
        name=item['patch']
        if not isinstance(name,str) or PurePosixPath(name).name!=name or PureWindowsPath(name).drive or '\\' in name:raise ValueError('Invalid patch name')
        patchfile=patches/name
        if patchfile.is_symlink() or not patchfile.is_file():raise ValueError('Patch must be a regular file')
        if hashlib.sha256(patchfile.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('Patch hash mismatch')
        apply(applied,patchfile)
    if inventory(applied)!=expected:raise ValueError('Applied series differs from candidate bytes/executable modes')
    combined=output/'applied-combined';copy(base,combined)
    apply(combined,patches.parent/f'agent-docs-kit-v{version}.patch')
    if inventory(combined)!=expected:raise ValueError('Combined patch differs from candidate')
    archive=patches.parent/f'agent-docs-kit-v{version}.zip'
    extracted=extract(archive,output/'extracted',f'agent-docs-kit-v{version}')
    if inventory(extracted)!=expected:raise ValueError('Archive differs from candidate')
    verify_manifest(extracted)
    before=inventory(extracted)
    command=[str(python),'-B','-m','unittest','discover','-s','tests','-v']
    start=time.monotonic()
    with (output/'fresh-zip-suite.log').open('x') as log:
        try:
            run=subprocess.run(command,cwd=extracted,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
            code=run.returncode
        except subprocess.TimeoutExpired:code=124
    text=(output/'fresh-zip-suite.log').read_text(errors='replace')
    match=re.search(r'^Ran (\d+) tests? in ([\d.]+)s$',text,re.M)
    skipped=re.search(r'^OK \(skipped=(\d+)\)$',text,re.M)
    clean=inventory(extracted)==before
    stable_base=inventory(base)==initial_base
    stable_candidate=inventory(candidate)==expected
    report={'version':version,'manifest_entries':count,'distribution_files':len(expected),
            'patches':len(names),'series_equal':True,'combined_equal':True,'archive_equal':True,
            'comparison':'SHA-256 content and executable bit; Git does not encode other POSIX permission bits',
            'extracted_tree_unchanged_by_tests':clean,'baseline_unchanged':stable_base,'candidate_unchanged':stable_candidate,
            'suite':{'command':command,'exit':code,'tests':int(match[1]) if match else None,
                     'skipped':int(skipped[1]) if skipped else 0,'graphify_requested':graphify,
                     'seconds':round(time.monotonic()-start,3),'log':'fresh-zip-suite.log'},
            'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
    report['state']='passed' if code==0 and match and clean and stable_base and stable_candidate else 'failed'
    with (output/'verification.json').open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--base',type=Path,required=True)
    parser.add_argument('--candidate',type=Path,required=True)
    parser.add_argument('--patches',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--python',type=Path,default=Path(sys.executable))
    parser.add_argument('--graphify',action='store_true')
    parser.add_argument('--timeout',type=int,default=1200)
    a=parser.parse_args()
    if a.timeout<=0:parser.error('timeout must be positive')
    result=verify(a.base,a.candidate,a.patches,a.output,a.python,a.graphify,a.timeout)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if result['state']=='passed' else 1)
