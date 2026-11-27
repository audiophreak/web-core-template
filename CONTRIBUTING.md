# Contributing

Thanks for your interest in web-core-template. It is a small, independently maintained project, so
focused changes with tests are the easiest to review.

## Development setup

Requirements: Git, [uv](https://docs.astral.sh/uv/), Python 3.12 or later, Node.js 26 (for the
end-to-end tests) and Docker (for the database tests).

```bash
git clone https://github.com/audiophreak/web-core-template.git
cd web-core-template
uv sync --locked
uv run pytest -m "not e2e"   # fast tests
uv run pytest                # everything: generates real apps and runs npm ci / npm run check
```

## Ground rules

The full rules are in [AGENTS.md](AGENTS.md); they apply to people and coding agents alike. In short:

- Open an issue first for anything beyond a small fix.
- Any change under `template/` or to `copier.yml` needs a matching test, and with every feature off
  the output must stay byte-identical to the previous release (`tools/compare_release.py <tag>`).
- Do not add, upgrade or remove dependencies without discussion; pins are exact and at least two
  weeks old. Regenerate lockfiles only with `tools/refresh_locks.py`.
- Generated output must never contain secrets, personal identities or machine-specific paths.
- Never add recursive or forced deletes or hidden errors. Record user-visible changes in
  `CHANGELOG.md`.

## Pull requests

CI runs the full suite on Linux (Python 3.12 and 3.14, real npm builds) and the database tests for
every pull request. Releases are annotated git tags created by the maintainer after CI passes on
`main` (`docs/maintaining.md`).

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE).
Report security issues privately as described in [SECURITY.md](SECURITY.md).
