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
- A lockfile must match `package.json` exactly for every supported configuration. Lockfiles live in
  `locks/`, one per distinct npm dependency set; regenerate them with `tools/refresh_locks.py`, never by
  hand, and never add resolution or install steps to generation.
- A feature is a Copier question plus an entry in `features.yml` plus conditionally named files; follow
  the checklist in `docs/features.md`. With every feature off, output must stay byte-identical to the
  previous release (apart from `.copier-answers.yml`); run `tools/compare_release.py <tag>`.
- New dependencies must be exact-pinned, at least two weeks old, and must not add install scripts.

## Destructive commands

An unscoped delete is only as safe as the path typed into it, so these are rules, not preferences:

- **Never** run recursive or forced removal by hand (`rm -r`, `rm -rf`), `find ... -delete`,
  `git clean`, `git reset --hard`, forced pushes or recursive `chmod`/`chown`. A test fails if any
  appear in the repository's tools, tests, CI or documented shell snippets.
- **Prefer not deleting.** Make a fresh, unique directory for every run (`mktemp -d`, Python's
  `tempfile`, or pytest's `tmp_path`) instead of clearing and reusing one, and leave cleanup to the
  operating system.
- **If something must be removed,** ask first, and use the validated helper in the
  `developer-platform` repository (`tools/scratch.py clean`), which deletes only paths provably
  inside a workspace it created. Never write a literal system path, or a variable that might be
  empty, after a delete command.
- **Never hide a state-changing command's errors** (`2>/dev/null`); a failed delete must be visible.
- **Tests of destructive behaviour use pure validators.** Never point anything that can delete at a
  system path, not even one expected to be refused; test refusals with `assert_removable`.
- Anything that would remove user data, a repository, a tag or a branch needs the user's explicit
  approval first.

## Secure coding

- Treat every answer as untrusted: validate it in `copier.yml`, and never render it into code or
  data without a safe filter (`to_json` for JSON, restricted characters for prose).
