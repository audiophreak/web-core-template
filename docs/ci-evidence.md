# CI test evidence

Every CI run uploads a small, machine-readable record of what was tested, so a result can be checked
independently of the console log. Nothing here changes whether the build passes: the job still fails
exactly when pytest fails.

## What a run produces

One artifact per Python version (3.12 and 3.14), named `test-reports-py<version>-<commit>`, where
`<commit>` is the full SHA of the commit under test. It is uploaded **even when the tests fail**, and
kept for **30 days**.

| File | Contents |
| ---- | -------- |
| `junit.xml` | JUnit XML from pytest. Suite name `web-core-template@<commit>-py<version>`; the `tests`, `failures`, `errors` and `skipped` attributes are the counts; each non-passing `testcase` has a `failure`, `error` or `skipped` child with the message or skip reason. Captured output is not included. |
| `pytest-output.txt` | The full console output, with a short summary of every failure, error and skip. |
| `metadata.txt` | `commit_under_test`, `github_sha`, `ref`, `event`, `run_id`, `run_attempt`, `runner_os`, Python, Node, npm, `uv` and pytest versions, and SHA-256 of `uv.lock`, `locks/base.json` and `locks/zod.json`. |
| `installed-packages.txt` | Exact Python package versions installed for the run. |
| `generated-apps/<app>.check.txt` | Output of `npm run check` (lint, type check, format check, tests, build) inside each generated app of the end-to-end tests. Recorded before the verdict, so it is present even when the gate fails. |
| `generated-apps/<app>.check-exit-code.txt` | The exit status of that `npm run check`; non-zero means the gate failed (and the test failed). |
| `generated-apps/<app>.audit.json` and `<app>.audit-exit-code.txt` | The `npm audit --omit=dev --json` result of each generated app and its exit code. It is **recorded, not asserted**: it documents the production dependency state at that time and never changes a verdict. |

The run's page also shows a **job summary**: the last 60 lines of the console record (pass, fail and
skip counts, with reasons).

**Which commit?** On a pull request GitHub tests a synthetic merge of the branch into `main`
(`github_sha`). The evidence is tied to the *branch tip you reviewed* (`commit_under_test`, also in
the artifact and suite names). Both are recorded.

## The database job

The tests that need Docker (marker `db`: PostgreSQL containers and the local Supabase stack) run in their own
job, `database`, once on Python 3.14, so the matrix jobs stay fast. The job sets `REQUIRE_DOCKER=1`, which makes
those tests **fail, not skip,** when Docker is missing. Its artifact is `test-reports-database-<commit>`, with the
same files and the same quiet token scan as the others; no database dump or connection string is ever written
to the reports, and the tests use throwaway containers, volumes and passwords created for the run.

## Retrieving and reading it

```bash
gh run list --commit <full-sha>                       # find the run for a commit
gh run download <run-id> --dir <empty-directory>      # every artifact of the run, one folder each
gh run download <run-id> --name test-reports-py3.14-<full-sha> --dir <empty-directory>
```

Use a fresh empty directory (for example `mktemp -d`). A green run shows `failures="0"` and
`errors="0"` in `junit.xml`; compare `commit_under_test` with the commit you are reviewing. To list
non-passing cases:

```python
import sys, xml.etree.ElementTree as ET

suite = ET.parse(sys.argv[1]).getroot()
suite = suite if suite.tag == "testsuite" else suite.find("testsuite")
print({k: suite.get(k) for k in ("name", "tests", "failures", "errors", "skipped")})
for case in suite.iter("testcase"):
    for kind in ("failure", "error", "skipped"):
        found = case.find(kind)
        if found is not None:
            print(kind, case.get("classname"), case.get("name"), (found.get("message") or "")[:100])
```

## Skipped tests

None are expected in CI: Node is set up from `template/.nvmrc`, so the end-to-end tests run. Any skip
appears in `junit.xml` and the summary with its reason.

## Security

- The workflow keeps `contents: read`, pinned actions and no secrets; the artifact upload uses the
  runner's own scoped token.
- Reports are produced by pytest, `npm` and a few shell lines that write named fields. **Nothing dumps
  the environment**, and JUnit logging is off. Gate output can quote project files, so treat the
  artifacts as repository-internal (in a private repository, visible to those who can read it).
- Before upload, the reports are scanned for token-shaped strings (GitHub tokens, private-key
  headers, AWS key ids) with a **quiet** `grep -q`: the matching text is never printed. A hit, or a
  scan that cannot run, fails the job with a generic error and **skips the upload**. The job summary
  is written only after the scan passes. A regression test runs the workflow's own scan on fake
  credentials in a disposable directory and checks that it rejects them without printing them.
- `tests/test_ci_workflow.py` fails if the workflow stops uploading on failure, drops the scan, widens
  the artifact path, extends retention beyond 30 days, adds a credential or dumps the environment.

## Limitations

- **The scan protects the artifact, not the log.** Test output is shown live in the job log while
  pytest runs (`tee`), before any scan can run; a secret that a test prints would be in that log
  regardless. GitHub masks only secrets it knows (registered secrets), and this project stores none.
  The rule is: tests must never print credentials. The scan is a backstop for the uploaded files.
- The scan recognizes a few common shapes, not every secret, and does not decode or unpack anything.
- Evidence, not attestation: whoever can change the workflow can change what it reports, and nothing
  signs the artifacts. Branch protection (not enabled) would require review of workflow changes.
- Artifacts expire after 30 days; keep a copy of what a release record needs.
- The audit is informational; a new advisory does not fail CI. No coverage or security-analysis
  reports are collected.
