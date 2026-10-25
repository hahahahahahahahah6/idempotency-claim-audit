import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from idempotency_claim_audit.audit import audit, extract_claims, extract_evidence, normalize
from idempotency_claim_audit.__main__ import main

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures'


class ClaimTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize("  ISN'T Safe-to  RE-RUN "), 'is not safe to re run')


def claim_test(text, expected):
    def test(self):
        self.assertEqual({c['family'] for c in extract_claims(text)}, set(expected))
    return test


CLAIMS = [
 ('idempotent', ['idempotent']),
 ('safe to run twice', ['idempotent']),
 ('safe to rerun', ['idempotent']),
 ('safe to re run', ['idempotent']),
 ('running it twice is safe', ['idempotent']),
 ('re running is safe', ['idempotent']),
 ('rerunning is safe', ['idempotent']),
 ('overwrites', ['overwrite']),
 ('replaces the existing', ['overwrite']),
 ('does not append', ['overwrite']),
 ('never appends', ['overwrite']),
 ("doesn't append", ['overwrite']),
 ('safe to re-run', ['idempotent']),
 ('re-running is safe', ['idempotent']),
 ('SAFE   TO\nRUN TWICE', ['idempotent']),
 ('not idempotent', []),
 ('is not idempotent', []),
 ("isn't idempotent", []),
 ('no idempotent promise', []),
 ('never idempotent', []),
 ('without idempotent behavior', []),
 ('Idempotency notes', []),
 ('appends to the log', []),
 ('safe to run again', []),
 ('safe to twice', []),
 ('unidempotent', []),
 ('overwritesomething', []),
 ('`idempotent`', ['idempotent']),
 ('```text\nidempotent\n```', []),
 ('~~~md\noverwrites\n~~~', []),
 ('````text\nidempotent\n```\noverwrites\n````', []),
 ('```text\nidempotent', []),
 ('Not idempotent. It overwrites output.', ['overwrite']),
 ('It is idempotent. It never appends.', ['idempotent', 'overwrite']),
 ('Not idempotent. It is safe to rerun.', ['idempotent']),
 ('It never appends and is idempotent.', ['overwrite']),
 ('No output; it overwrites files.', ['overwrite']),
]
for index, (text, expected) in enumerate(CLAIMS):
    setattr(ClaimTests, 'test_phrase_%02d' % index, claim_test(text, expected))


class EvidenceTests(unittest.TestCase):
    def test_dedupe_same_line_kind(self):
        items = extract_evidence('open("a", "a"); open("b", "a")', 'main.py')
        self.assertEqual(len(items), 1)

    def test_distinct_lines(self):
        self.assertEqual(len(extract_evidence('open("a", "a")\nopen("b", "a")', 'main.py')), 2)

    def test_location(self):
        self.assertEqual(extract_evidence('\nopen("a", "a")', 'x.py'),
                         [{'path': 'x.py', 'line': 2, 'kind': 'append-open'}])


def evidence_test(source, expected):
    def test(self):
        self.assertEqual([e['kind'] for e in extract_evidence(source, 'main.py')], expected)
    return test


EVIDENCE = [
 ('open(p, "a")', ['append-open']),
 ('open(p, "a+")', ['append-open']),
 ('open(p, "ab")', ['append-open']),
 ('open(p, "at")', ['append-open']),
 ('open(p, mode="a+")', ['append-open']),
 ('Path(p).open("a")', ['append-open']),
 ('p.open(mode="a")', ['append-open']),
 ('import io\nio.open(p, "a")', ['append-open']),
 ('import io as streams\nstreams.open(p, "a")', ['append-open']),
 ('import io as streams\nstreams.open(p, mode="a")', ['append-open']),
 ('open(p, "w")', ['overwrite-open']),
 ('open(p, "w+")', ['overwrite-open']),
 ('open(p, "wb")', ['overwrite-open']),
 ('open(p, "wt")', ['overwrite-open']),
 ('p.open("w")', ['overwrite-open']),
 ('import io as streams\nstreams.open(p, "w")', ['overwrite-open']),
 ('p.write_text("stuff")', ['path-write']),
 ('p.write_bytes(b"stuff")', ['path-write']),
 ('open(p, "x")', []),
 ('open(p, mode)', []),
 ('p.open(mode=mode)', []),
 ('open(p)', []),
 ('os.remove(p)\nos.replace(p, q)\nshutil.copy(p, q)', []),
 ('print("a", file=f)', []),
 ('sql = "INSERT INTO events VALUES (1)"', ['insert']),
 ('sql = "insert into events values (1)"', ['insert']),
 ('sql = "INSERT OR IGNORE INTO events VALUES (1)"', []),
 ('sql = "INSERT INTO events VALUES (1) ON CONFLICT DO NOTHING"', []),
 ('sql = "INSERT OR REPLACE INTO events VALUES (1)"', []),
 ('sql = "insert into t; or ignore"', []),
 ('sql = "insert into t; or replace"', []),
 ('"INSERT INTO events VALUES (1)"', []),
 ('def f():\n    "INSERT INTO events VALUES (1)"', []),
 ('class C:\n    "INSERT INTO events VALUES (1)"', []),
 ('async def f():\n    "INSERT INTO events VALUES (1)"', []),
 ('def f():\n    pass\n    "INSERT INTO events VALUES (1)"', ['insert']),
 ('def open(*args): pass\nopen(p, "a")', []),
 ('class open: pass\nopen(p, "a")', []),
 ('def f(open): pass\nopen(p, "a")', []),
 ('async def open(): pass\nopen(p, "a")', []),
 ('def open(): pass\np.open("a")', ['append-open']),
 ('def open(): pass\nimport io\nio.open(p, "a")', ['append-open']),
 ('if False:\n    open(p, "a")', ['append-open']),
 ('def uncalled():\n    open(p, "a")', ['append-open']),
 ('sql = "INSERT INTO t"\nother = "INSERT INTO u"', ['insert', 'insert']),
]
for index, (source, expected) in enumerate(EVIDENCE):
    setattr(EvidenceTests, 'test_shape_%02d' % index, evidence_test(source, expected))


class TreeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, path, content):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding='utf-8')

    def setup_tree(self, doc='idempotent', source='pass'):
        self.write('README.md', doc)
        self.write('main.py', source)

    def test_no_claim(self):
        self.setup_tree('Just a tool.', 'open(p,"a")')
        self.assertEqual(audit(self.root)['findings'], [])

    def test_both_independent(self):
        self.setup_tree('idempotent. overwrites.', 'open(p,"a")')
        self.assertEqual(len(audit(self.root)['findings']), 2)

    def test_no_source_both(self):
        self.write('README.md', 'idempotent. overwrites.')
        findings = audit(self.root)['findings']
        self.assertEqual(len(findings), 2)
        self.assertTrue(all(f['severity'] == 'HIGH' and f['evidence'] == [{'kind': 'no-source'}] for f in findings))

    def test_no_source_test_append_stays_high(self):
        self.write('README.md', 'idempotent')
        self.write('tests/test_a.py', 'open(p,"a")')
        self.assertEqual(audit(self.root)['findings'][0]['note'], 'no-source')

    def test_missing_overwrite(self):
        self.setup_tree('overwrites')
        f = audit(self.root)['findings'][0]
        self.assertEqual((f['severity'], f['note']), ('HIGH', 'no overwrite evidence'))

    def test_testonly_overwrite(self):
        self.setup_tree('overwrites')
        self.write('fixtures/work.py', 'open(p,"a")')
        self.assertEqual(audit(self.root)['findings'][0]['rule'], 'documented-overwrite-claim-false-test-only')

    def test_production_overwrite_wins(self):
        self.setup_tree('overwrites', 'p.write_text("a")')
        self.write('tests/work.py', 'open(p,"a")')
        result = audit(self.root)
        self.assertEqual(result['findings'], [])
        self.assertEqual(result['verified_claims'][0]['family'], 'overwrite')

    def test_append_wins_overwrite(self):
        self.setup_tree('overwrites', 'open(p,"a")\nopen(p,"w")')
        f = audit(self.root)['findings'][0]
        self.assertEqual([e['kind'] for e in f['evidence']], ['append-open'])

    def test_all_append_evidence_one_finding(self):
        self.setup_tree('idempotent. idempotent.', 'open(p,"a")\nsql="insert into t"')
        result = audit(self.root)
        self.assertEqual(len(result['findings']), 1)
        self.assertEqual(len(result['findings'][0]['evidence']), 2)

    def test_ignore_evidence(self):
        self.setup_tree(source='open(p,"a")')
        self.write('other.py', 'pass')
        self.assertEqual(audit(self.root, ['main.py'])['findings'], [])

    def test_ignore_docs(self):
        self.setup_tree(source='open(p,"a")')
        self.assertEqual(audit(self.root, ['README.md'])['claims'], [])

    def test_ignore_directory(self):
        self.setup_tree()
        self.write('generated/worker.py', 'open(p,"a")')
        self.assertEqual(audit(self.root, ['generated'])['findings'], [])

    def test_docs_nested(self):
        self.setup_tree('No promise', 'open(p,"a")')
        self.write('docs/nested/guide.md', 'idempotent')
        self.assertEqual(len(audit(self.root)['findings']), 1)

    def test_other_markdown_not_claim(self):
        self.setup_tree('No promise', 'open(p,"a")')
        self.write('notes.md', 'idempotent')
        self.assertEqual(audit(self.root)['claims'], [])

    def test_symlink_file(self):
        self.setup_tree()
        self.write('ignored.txt', 'open(p,"a")')
        (self.root/'linked.py').symlink_to(self.root/'ignored.txt')
        self.assertEqual(audit(self.root)['findings'], [])

    def test_symlink_directory(self):
        self.setup_tree()
        with tempfile.TemporaryDirectory() as other:
            Path(other, 'bad.py').write_text('open(p,"a")')
            (self.root/'linked').symlink_to(other, target_is_directory=True)
            self.assertEqual(audit(self.root)['findings'], [])

    def test_env_never_opened(self):
        self.setup_tree()
        self.write('.env', 'ENV_MARKER_MUST_NEVER_APPEAR_5823')
        original = io.open
        def guarded(file, *args, **kwargs):
            if isinstance(file, (str, Path)) and Path(file).name == '.env':
                self.fail('.env was opened')
            return original(file, *args, **kwargs)
        with patch('io.open', side_effect=guarded):
            result = audit(self.root)
        self.assertNotIn('ENV_MARKER_MUST_NEVER_APPEAR_5823', json.dumps(result))

    def test_syntax_error(self):
        self.setup_tree(source='def broken(')
        self.assertEqual(len(audit(self.root)['errors']), 1)

    def test_pep263_encoding(self):
        self.setup_tree()
        (self.root/'main.py').write_bytes(b'# coding: latin-1\ns="\xe9"\nopen(p,"a")')
        self.assertEqual(len(audit(self.root)['findings']), 1)

    def test_self_audit_no_claim(self):
        result = audit(ROOT)
        self.assertEqual(result['claims'], [])
        self.assertEqual(result['findings'], [])


def exclusion_test(path, test_only):
    def test(self):
        self.setup_tree()
        self.write(path, 'open(p,"a")')
        result = audit(self.root)
        self.assertEqual(len(result['findings']), 1 if test_only else 0)
        if test_only:
            self.assertEqual(result['findings'][0]['severity'], 'MEDIUM')
    return test


for index, (path, test_only) in enumerate([
    ('tests/work.py', True), ('fixtures/work.py', True), ('test_work.py', True),
    ('work_test.py', True), ('nested/tests/work.py', True),
    ('docs/work.py', False), ('examples/work.py', False),
    ('build/work.py', False), ('dist/work.py', False), ('.venv/work.py', False),
]):
    setattr(TreeTests, 'test_exclusion_%02d' % index, exclusion_test(path, test_only))


class CLITests(unittest.TestCase):
    def invoke(self, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = main(argv)
            except SystemExit as exc:
                code = exc.code
        return code, stdout.getvalue(), stderr.getvalue()

    def test_version(self):
        self.assertEqual(self.invoke(['--version'])[:2], (0, '0.1.0\n'))

    def test_help(self):
        code, out, err = self.invoke(['--help'])
        self.assertEqual(code, 0)
        self.assertIn('--ignore', out)

    def test_usage_error(self):
        self.assertEqual(self.invoke(['--unknown'])[0], 1)

    def test_bad_threshold(self):
        self.assertEqual(self.invoke(['--fail-on', 'LOW'])[0], 1)

    def test_nonexistent(self):
        self.assertEqual(self.invoke(['/nonexistent-idempotency-claim-audit'])[0], 1)

    def test_medium_high_threshold(self):
        code, out, _ = self.invoke([str(FIXTURES/'testonly'), '--fail-on', 'HIGH', '--json'])
        result = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(result['findings'][0]['severity'], 'MEDIUM')
        self.assertEqual(result['status'], 'PASS')

    def test_verbose(self):
        code, out, _ = self.invoke([str(FIXTURES/'good/idempotent'), '--verbose'])
        self.assertEqual(code, 0)
        self.assertIn('verified idempotent', out)

    def test_json_shape(self):
        code, out, _ = self.invoke([str(FIXTURES/'good/overwrite'), '--json'])
        result = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(result['findings'], [])
        self.assertEqual(result['verified_claims'][0]['family'], 'overwrite')

    def test_repeat_ignore(self):
        code, out, _ = self.invoke([str(FIXTURES/'bad/idempotent'), '--ignore', 'README.md', '--ignore', 'other.py'])
        self.assertEqual(code, 0)

    def test_parse_error_json(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'main.py').write_text('def broken(')
            code, out, _ = self.invoke([directory, '--json'])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(out)['status'], 'ERROR')


def fixture_test(name, code, rule=None, severity=None):
    def test(self):
        actual_code, out, err = self.invoke([str(FIXTURES/name), '--json'])
        result = json.loads(out)
        self.assertEqual(actual_code, code)
        self.assertEqual(err, '')
        if rule:
            self.assertEqual(len(result['findings']), 1)
            self.assertEqual(result['findings'][0]['rule'], rule)
            self.assertEqual(result['findings'][0]['severity'], severity)
        else:
            self.assertEqual(result['findings'], [])
            self.assertEqual(len(result['verified_claims']), 1)
    return test


for index, args in enumerate([
    ('good/idempotent', 0), ('good/overwrite', 0),
    ('bad/idempotent', 2, 'documented-idempotent-claim-false', 'HIGH'),
    ('bad/overwrite', 2, 'documented-overwrite-claim-false', 'HIGH'),
    ('bad/insert', 2, 'documented-idempotent-claim-false', 'HIGH'),
    ('testonly', 2, 'documented-idempotent-claim-false-test-only', 'MEDIUM'),
]):
    setattr(CLITests, 'test_fixture_%02d' % index, fixture_test(*args))


if __name__ == '__main__':
    unittest.main()
