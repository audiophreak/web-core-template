# Updating a generated project

Generated projects record where they came from in `.copier-answers.yml` (`_src_path` and
`_commit`). Commit that file and never edit it by hand.

The template repository is private: `copier update` needs git credentials that work without a
prompt (for example after `gh auth setup-git`), and fails with a git authentication error otherwise.

Copier updates by three-way merge between the release the project was generated from, the release
you update to, and your current files, so your edits are kept. It needs a clean git working tree.

```bash
git status                              # must be clean; commit or stash first
copier update --vcs-ref v0.1.1 --defaults --skip-answered
git diff                                # review; resolve any <<<<<<< inline conflict markers
npm install                             # regenerate package-lock.json after dependency changes
npm run check
git add -A && git commit -m "chore: update project template to v0.1.1"
```

**Always pass `--vcs-ref` with the release you chose.** Without it Copier takes the newest tag,
which can be a breaking release (a new Node major, renamed questions). Read `CHANGELOG.md` first.
If you generated the project with `devkit`, take the release from the range your devkit supports.

**Optional features survive upgrades.** A project's feature selections are recorded in
`.copier-answers.yml` (for example `zod: true`) and `copier update` reuses them, so an upgrade
never switches a feature on or off. Features introduced by a newer release take their default
(off). To switch one on later:

```bash
copier update --vcs-ref v0.2.0 --data zod=true --defaults --skip-answered
npm ci                                  # the lockfile now matches package.json
npm run check
```

`database` (`--data database=postgres` or `supabase`) can be switched on from `none` the same way; it needs
`zod`, adds files, dependencies and the matching lock, and keeps your work and your own migrations (each
migration has its own lock file, so they never conflict with a template migration). **Do not change an
established provider this way**: it swaps the tooling under the project and orphans the old database's
data; see `docs/features.md`. `forms` and `rest_api` can be switched on the same way (`--data forms=true`, `--data rest_api=true`),
but only for a project that already has `zod`; without it the update is refused and nothing changes.
`rest_api` adds files only (no dependency, the lockfile is untouched), keeps any `src/app/api/` routes
you wrote yourself and leaves your Forms selection exactly as it was.

`auth` (`--data auth=true`) can be switched on the same way for a project that already has `zod`, `rest_api` and a
`database`; if any is missing the update is refused and nothing changes. It adds the files, `better-auth`, an
`.npmrc` and the matching lock, replaces the sample repository with the owner-scoped one, and adds two
migrations after yours. Then run `npm ci`, `npm run db:init` (it appends the runtime password and the
authentication secret to your `.env.local` and changes nothing else) and `npm run db:migrate`. Rows in
`app.items` from before authentication have no owner and are invisible to the application until you assign
them (see the generated `docs/auth.md`). **Disabling authentication is not an upgrade**: a raw
`copier update --data auth=false` removes the generated files but not the data, and the migrations and
accounts need a decision of yours; `devkit upgrade` refuses it.

Only booleans are accepted (`true`/`false`, `yes`/`no`, `on`/`off`); anything else is rejected before
anything changes. Switching a feature off again is not automated; remove its files and dependency by
hand (or regenerate).

After any update that changes dependencies, `npm ci` (not `npm install`) should succeed because the
template ships a lockfile matching the new `package.json` for your feature selection; if the merge
left conflict markers in `package.json` or `package-lock.json`, resolve `package.json` first and run
`npm install` to repair the lockfile.

Notes:

- Conflicts are written inline (`<<<<<<<`, `=======`, `>>>>>>>`) and the command still exits 0:
  search for the markers before committing. Nothing is silently lost (tests cover this).
- `package.json` and the lockfile conflict most often. Resolve `package.json` first, then run
  `npm install` instead of merging the lockfile by hand.
- Questions added after your project was generated take their defaults; override one with
  `--data NAME=VALUE`.
- If the template moves, edit `_src_path` in `.copier-answers.yml` to the new URL.
