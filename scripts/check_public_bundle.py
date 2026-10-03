#!/usr/bin/env python3
"""Check staged source and history without printing credential values."""
import argparse
import fnmatch
import os
from pathlib import Path
import re
import subprocess


FORBIDDEN_FILES = ('*.key', '*.pem', '*.p12', '*.pfx', 'credentials*.json', 'secrets*.json', 'generation-log.json', 'job.json', 'reference-original', '*.log')
FORBIDDEN_DIRS = {'outputs', 'exports', 'source', '.venv', '__pycache__', 'ui-backups'}
PATTERNS = [
    (b'(?:ghp_|gho_|github_pat_)[A-Za-z0-9_]{20,}', 'GitHub token'),
    (b'AKIA[A-Z0-9]{16}', 'access key'),
    (b'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', 'private key'),
    (b'https?://[^/\\s\"\']+\\.ling\\.ai', 'private service URL'),
    (b'https?://(?:10\\.[0-9.]+|192\\.168\\.[0-9.]+|172\\.(?:1[6-9]|2[0-9]|3[01])\\.[0-9.]+)', 'private network endpoint'),
    (b'/Users/[A-Za-z0-9_-]+/', 'personal absolute path'),
    (b'https?://[^/\\s\"\']+:[^/\\s\"\']+@', 'URL with embedded credentials'),
]


def secret_needles():
    values = [os.environ.get('ARK_API_KEY', '')]
    file = Path(os.environ.get('ARK_API_KEY_FILE', Path.home()/'.config/cartoon-character-animator/ark.key')).expanduser()
    if file.is_file():
        values.append(file.read_text().strip())
    return [v.encode() for v in values if len(v) >= 12]


def inspect_blob(name, data, needles):
    findings = []
    path = Path(name)
    if path.name != '.env.example' and (path.name.startswith('.env') or any(fnmatch.fnmatch(path.name,p) for p in FORBIDDEN_FILES)):
        findings.append('credential or runtime filename')
    if FORBIDDEN_DIRS.intersection(path.parts):
        findings.append('runtime directory')
    if any(value in data for value in needles):
        findings.append('matches a local credential')
    for expression, reason in PATTERNS:
        if re.search(expression,data):
            findings.append(reason)
    return findings


def git(root, *args):
    return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.DEVNULL)


def check(root, staged, history, needles):
    failures=[]
    if staged:
        names=[n.decode() for n in git(root,'ls-files','--cached','-z').split(b'\0') if n]
        blobs=((name,git(root,'show',':'+name)) for name in names)
    else:
        files=[p for p in root.rglob('*') if p.is_file() and '.git' not in p.relative_to(root).parts and '__pycache__' not in p.relative_to(root).parts]
        names=[str(p.relative_to(root)) for p in files]
        blobs=((name,(root/name).read_bytes()) for name in names)
    for name,data in blobs:
        for reason in inspect_blob(name,data,needles):failures.append(f'{name}: {reason}')
    history_count=0
    if history:
        for line in git(root,'rev-list','--objects','--all').decode().splitlines():
            parts=line.split(' ',1)
            if len(parts)!=2:continue
            oid,name=parts
            if git(root,'cat-file','-t',oid).strip()!=b'blob':continue
            history_count+=1
            for reason in inspect_blob(name,git(root,'cat-file','blob',oid),needles):
                failures.append(f'history {oid[:8]} {name}: {reason}')
    return names,history_count,failures


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--path',type=Path,default=Path('.'))
    parser.add_argument('--staged',action='store_true')
    parser.add_argument('--history',action='store_true')
    args=parser.parse_args()
    try:
        names,count,failures=check(args.path.resolve(),args.staged,args.history,secret_needles())
    except (OSError,subprocess.CalledProcessError):
        parser.exit(2,'Cannot read release files or Git metadata; scan did not complete.\n')
    if failures:
        print('\n'.join(sorted(set(failures))))
        parser.exit(1,'Public bundle scan failed; no credential values were displayed.\n')
    print(f'PASS: {len(names)} source files, {count} history blobs; no matched credentials, private endpoints or runtime files.')


if __name__=='__main__':main()
