# Project rules

This repository is a Copier template (`copier.yml` + `template/`), versioned by git tags. It is not
a generated application; generated applications carry their own `AGENTS.md` from `template/`.

## Commands

- `uv sync` — create the project-local environment from `uv.lock`
- `uv run pytest -m "not e2e"` — fast tests
- `uv run pytest` — everything, including generating real apps and running `npm ci` and
  `npm run check` in them (the gate for any change under `template/`)

## Working agreement

- Plan first for anything beyond a trivial fix; wait for approval on breaking changes.
- Keep changes small and focused. Do not add, upgrade or remove dependencies without approval;
  pin exact versions (`pyproject.toml`, `uv.lock`, and the npm pins and lockfile in `template/`)
  and keep the two-week release-age rule.
- Do not commit, push, tag, publish or rewrite history unless explicitly asked. Never move or
  delete a published tag.
- Work is not done until `uv run pytest` passes. Report actual results, including failures.
- Any change under `template/` or to `copier.yml` needs a matching test. Record user-visible
  changes in `CHANGELOG.md`; decide the version bump with the rules in `README.md`.

## Template rules

- Generated projects must contain no secrets, credentials, personal identities, usernames,
  home-directory paths or references to any other application. Tests enforce this.
- Only files that need substitution get the `.jinja` suffix; keep the rest verbatim.
- No Copier `_tasks`, `_migrations` or `_jinja_extensions` without an explicit decision: they run
  code and force `--trust`. A test enforces this.
- Validation belongs in `copier.yml` (it is the only place; there is no mirror to keep in sync).
- A lockfile in `template/` must match `package.json` exactly; tests check this.

## Secure coding

- Treat every answer as untrusted: validate it in `copier.yml`, and never render it into code or
  data without a safe filter (`to_json` for JSON, restricted characters for prose).
