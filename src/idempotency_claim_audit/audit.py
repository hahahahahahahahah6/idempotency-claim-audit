"""Offline documentation and AST analysis. Never imports audited source."""
import ast
import fnmatch
import os
from pathlib import Path
import re
import tokenize

PHRASES = {
    'idempotent': ('idempotent', 'safe to run twice', 'safe to rerun',
                   'safe to re run', 'running it twice is safe',
                   're running is safe', 'rerunning is safe'),
    'overwrite': ('overwrites', 'replaces the existing', 'does not append',
                  'never appends'),
}


def normalize(text):
    return ' '.join(text.lower().replace("n't", ' not').replace('-', ' ').split())


def extract_claims(text, path='README.md'):
    """Return one location per family per prose sentence, excluding fences."""
    prose = []
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        match = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not line[match.end():].strip():
                fence = None
            continue
        if match:
            fence = match[1]
            continue
        prose.append((number, line))
    # Blank lines and sentence terminators delimit prose; wrapped lines remain joined.
    claims = []
    buffer = ''
    start = 1
    def collect(sentence, lineno):
        normalized = normalize(sentence)
        for family, phrases in PHRASES.items():
            if family == 'idempotent' and re.search(r'\b(no|not|never|without)\b', normalized):
                continue
            if any(re.search(r'\b' + re.escape(phrase) + r'\b', normalized) for phrase in phrases):
                claims.append({'family': family, 'path': path, 'line': lineno})
    for number, line in prose:
        if not buffer:
            start = number
        buffer += ' ' + line
        if not line.strip():
            collect(buffer, start)
            buffer = ''
        else:
            parts = re.split(r'[.!?]+', buffer)
            for part in parts[:-1]:
                collect(part, start)
                start = number
            buffer = parts[-1]
    collect(buffer, start)
    return claims


def extract_evidence(source, path):
    tree = ast.parse(source, filename=path)
    shadowed = any(
        (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == 'open')
        or (isinstance(n, ast.arg) and n.arg == 'open') for n in ast.walk(tree))
    io_aliases = {'io'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            io_aliases.update(alias.asname or 'io' for alias in node.names if alias.name == 'io')
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.body and isinstance(node.body[0], ast.Expr):
                value = node.body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    docstrings.add(id(value))
    evidence = {}
    def add(node, kind):
        key = (path, node.lineno, kind)
        evidence[key] = {'path': path, 'line': node.lineno, 'kind': kind}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            builtin = isinstance(func, ast.Name) and func.id == 'open' and not shadowed
            attribute = isinstance(func, ast.Attribute) and func.attr == 'open'
            if builtin or attribute:
                # Bound open methods take mode first; builtin and io.open take it second.
                io_open = attribute and isinstance(func.value, ast.Name) and func.value.id in io_aliases
                index = 1 if builtin or io_open else 0
                mode = node.args[index] if len(node.args) > index else None
                for keyword in node.keywords:
                    if keyword.arg == 'mode':
                        mode = keyword.value
                if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
                    if 'a' in mode.value:
                        add(node, 'append-open')
                    if 'w' in mode.value:
                        add(node, 'overwrite-open')
            if isinstance(func, ast.Attribute) and func.attr in ('write_text', 'write_bytes'):
                add(node, 'path-write')
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            value = node.value.lower()
            if 'insert into' in value and not any(guard in value for guard in ('or ignore', 'or replace', 'on conflict')):
                add(node, 'insert')
    return sorted(evidence.values(), key=lambda item: (item['path'], item['line'], item['kind']))


def audit(root, ignore=()):
    root = Path(root)
    if not root.is_dir():
        raise ValueError('audit path must be a directory')
    claims, production, testonly, errors = [], [], [], []
    source_count = 0
    def ignored(path):
        return any(fnmatch.fnmatchcase(path, pattern) for pattern in ignore)
    for base, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = sorted(d for d in directories if not (Path(base) / d).is_symlink()
                                and d not in ('.git', '__pycache__', '.venv', 'venv', 'build', 'dist')
                                and not d.endswith('.egg-info')
                                and not ignored((Path(base) / d).relative_to(root).as_posix()))
        for name in sorted(filenames):
            file = Path(base) / name
            relative = file.relative_to(root).as_posix()
            if file.is_symlink() or ignored(relative) or name == '.env' or name.startswith('.env.'):
                continue
            parts = file.relative_to(root).parts
            doc = relative == 'README.md' or (parts[0] == 'docs' and name.endswith('.md'))
            testing = any(part in ('tests', 'fixtures') for part in parts[:-1]) or name.startswith('test_') or name.endswith('_test.py')
            excluded = any(part in ('docs', 'examples') for part in parts[:-1])
            if not doc and (not name.endswith('.py') or excluded):
                continue
            try:
                if doc:
                    claims.extend(extract_claims(file.read_text(encoding='utf-8'), relative))
                else:
                    if not testing:
                        source_count += 1
                    with tokenize.open(str(file)) as stream:
                        items = extract_evidence(stream.read(), relative)
                    (testonly if testing else production).extend(items)
            except (OSError, UnicodeError, SyntaxError) as exc:
                errors.append({'path': relative, 'message': str(exc)})
    append = [e for e in production if e['kind'] in ('append-open', 'insert')]
    test_append = [e for e in testonly if e['kind'] in ('append-open', 'insert')]
    overwrite = [e for e in production if e['kind'] in ('overwrite-open', 'path-write')]
    findings, verified = [], []
    for family in PHRASES:
        locations = [c for c in claims if c['family'] == family]
        if not locations:
            continue
        severity, evidence, note = 'HIGH', [], ''
        if not source_count:
            note = 'no-source'
            evidence = [{'kind': 'no-source'}]
        elif append:
            evidence = append
        elif family == 'overwrite' and overwrite:
            verified.append({'family': family, 'claims': locations})
            continue
        elif test_append:
            severity, evidence = 'MEDIUM', test_append
        elif family == 'overwrite':
            note = 'no overwrite evidence'
        else:
            verified.append({'family': family, 'claims': locations})
            continue
        rule = 'documented-' + family + '-claim-false'
        if severity == 'MEDIUM':
            rule += '-test-only'
        findings.append({'rule': rule, 'severity': severity, 'family': family,
                         'claims': locations, 'evidence': evidence, 'note': note})
    return {'findings': findings, 'verified_claims': verified, 'claims': claims,
            'errors': errors, 'production_source_count': source_count}
