# idempotency-claim-audit

Offline audit of documented re-run claims against Python source.

Version 0.1.0. Python 3.9 or newer; standard library only at runtime.
The auditor compares documentation promises about repeated runs with static
file-writing and SQL evidence. It executes no audited code, uses no network,
and never reads `.env` contents. Reports go to stdout; diagnostics go to stderr.
The audited tree is read only. Symlinked files and directories are skipped.

## Usage

```sh
python3 -m pip install .
idempotency-claim-audit /path/to/project
idempotency-claim-audit /path/to/project --json
python3 -m idempotency_claim_audit /path/to/project --verbose
idempotency-claim-audit . --ignore 'generated/*' --ignore 'vendor/*' --fail-on HIGH
idempotency-claim-audit --version
```

The optional directory defaults to the current directory. `--ignore` accepts
repeatable, case-sensitive glob patterns matched against relative POSIX paths;
a matching directory prunes its subtree. `--verbose` adds claim locations and
verified families to text output. JSON always includes `findings`,
`verified_claims`, `claims`, `errors`, `production_source_count`, and `status`.
Each finding includes rule, severity, family, claim locations, evidence, and a
note. Evidence includes path, line, and kind, except the no-source sentinel.

Exit status: 0 for PASS, 2 for findings meeting the threshold, 1 for usage,
read, or parse errors. The default threshold is MEDIUM. `--fail-on HIGH`
retains MEDIUM findings in reports but permits a successful exit.

## Claims

Only root `README.md` and `docs/**/*.md` supply claims. Fenced code is
excluded; inline code counts as prose. Matching lowercases sentences,
expands `n't` to ` not`, changes hyphens to spaces, and collapses whitespace.
Exact phrases with word boundaries are required; no optional middle words
are inferred. These are the complete phrase families:

```text
idempotent family:
  idempotent
  safe to run twice
  safe to rerun
  safe to re run
  running it twice is safe
  re running is safe
  rerunning is safe

overwrite family:
  overwrites
  replaces the existing
  does not append
  never appends
```

Negation asymmetry: a sentence containing `no`, `not`, `never`, or `without`
suppresses the first family only. The second family retains negative
wordings as promises. Bare-word exclusions include `idempotency` and
append-positive prose such as “appends to the log”. Both families may be
present and are evaluated independently. This README keeps matching phrases
and rule names inside fences so its self-audit has no claims.

## Rules

| Family | Condition | Result |
| --- | --- | --- |
| First | Any production append evidence | HIGH, all append evidence |
| First | Only test append evidence | MEDIUM, all test append evidence |
| First | No append evidence anywhere | Verified |
| Second | Any production append evidence | HIGH, all append evidence |
| Second | Production content-replacement evidence, no production append | Verified |
| Second | No production evidence, test append evidence | MEDIUM, all test append evidence |
| Second | No production evidence or test append evidence | HIGH, no overwrite evidence note |
| Either | No production Python source | HIGH, no-source sentinel |
| Neither | Any code | PASS |

There is one finding per family, with append evidence taking precedence over
missing replacement evidence. Production replacement evidence wins over
test-only append evidence for the second family. No-source takes precedence
for either family. Repeated claims do not multiply findings.

```text
HIGH rules:
  documented-idempotent-claim-false
  documented-overwrite-claim-false
MEDIUM rules:
  documented-idempotent-claim-false-test-only
  documented-overwrite-claim-false-test-only
```

## Named evidence

| Kind | Static source shape |
| --- | --- |
| append-open | Literal mode containing `a` in builtin open, io.open (including module aliases), or receiver.open |
| insert | String literal containing `insert into` without `or ignore`, `or replace`, or `on conflict`, case insensitive |
| overwrite-open | Literal mode containing `w` in those open calls |
| path-write | Any receiver.write_text or receiver.write_bytes call |
| no-source | No production Python source available |

Builtin and io calls take mode as the second positional argument; bound
receiver methods take it as the first. The `mode` keyword also counts.
A file-local function, class, or parameter named `open` suppresses builtin
open evidence throughout that file. Evidence is deduplicated by file, line,
and kind. SQL literals are counted once per such location.

## Differentiation

| Neighbour | Its question compared with this tool |
| --- | --- |
| readonly-claim-audit | Whether filesystem writes occur at all, rather than repeat-run semantics |
| atomic-claim-audit | Single-write crash safety, rather than repeat-run safety |
| determinism-claim-audit | Reproducibility of one run, rather than composition of two runs |
| security-claim-audit | Execution safety, rather than append/insert evidence |
| bandit / ruff | Code issues, rather than documentation promises |
| Runtime double-run tester below | Executes functions twice, rather than comparing docs with source |
| idempotency-kit | Runtime idempotency-key library, rather than documentation auditing |

```text
Runtime double-run tester: pytest-idempotent
```

## Honest limitations

This is a literal-shape audit, not proof of repeat-run correctness.
Production source excludes `tests/`, `fixtures/`, `test_*.py`, `*_test.py`,
`docs/`, and `examples/`. Test and fixture source is collected separately;
docs and examples are never evidence. Build, distribution, virtual environment,
Git metadata, bytecode, and package metadata directories are skipped.

* Variable modes such as `open(path, mode)` are UNKNOWN and skipped.
* Exclusive mode `x` counts as neither append nor content replacement;
  it may fail on a repeated run.
* `os.remove`, `os.replace`, and `shutil.copy` are not content evidence here.
* `print(file=...)` handle provenance is not tracked, including append handles.
* Module, class, and function docstring SQL literals are excluded.
* Dead code counts. An uncalled append call still supplies evidence.
* One append item falsifies a whole first-family promise regardless of whether
  its actual content would duplicate: intentional existence over-approximation.
* Guarded INSERT shapes are excluded even in dead code. Guards are not proved.
* Receiver types are not inferred: unrelated methods with matching names can
  count. Imported builtin aliases and dynamically computed SQL are not resolved.
* Exact-phrase claims can miss paraphrases, and sentence-level negation can
  suppress an unrelated promise in the same sentence.
* Parse or read failures return status 1 and errors, rather than a clean bill.

## Development

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m pip install build
python3 -m build
# Offline fallback only if isolated build cannot obtain its requirements:
python3 -m build --no-isolation
PYTHONPATH=src python3 -m idempotency_claim_audit . --json
```

Setuptools builds the wheel and source distribution. Fixtures and unittest
sources are included in the source distribution through `MANIFEST.in`.
Licensed under MIT; see LICENSE.
