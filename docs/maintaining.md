# Maintaining the template

## Local development loop

Work in a checkout and render it as it is on disk, with no published repository involved:

```bash
uv sync
uv run pytest -m "not e2e"                               # fast checks
uv run copier copy --vcs-ref HEAD --defaults --data app_name=scratch-app . ../scratch-app
cd ../scratch-app && npm ci && npm run check             # or: uv run pytest -m e2e
```

`--vcs-ref HEAD` matters: once the repository has a tag, Copier otherwise renders the newest tag
and silently ignores your uncommitted work. Via the platform: `devkit new NAME --template
../web-core-template`.

Tests render from a snapshot copy in a temporary git repository, so they never depend on this
repository's commits, tags or uncommitted state.

## Refresh the dependency pins

Versions are written in `template/package.json.jinja`. The reference lockfile is
`locks/base.json`; every feature lockfile (`locks/<key>.json`) is derived from it
(`docs/features.md`).

1. Generate a sample app (above) with no features and `npm ci` it.
2. Change pins with `npm install --save-exact <package>@<version>`, choosing releases at least two
   weeks old, then `npm run check`.
3. Copy the updated `dependencies`, `devDependencies` into `template/package.json.jinja` (keeping
   its `{% if %}` blocks) and the lockfile into `locks/base.json`, restoring
   `{{ app_name | to_json }}` for the two name fields. If a feature has its own pin, change it in
   `package.json.jinja` too.
4. `uv run python tools/refresh_locks.py` regenerates every feature lockfile from the new base
   (`--check` first to see what would change). It stops if a result is not strictly additive.
5. `uv run pytest`: the tests fail if a pin is not exact, a lockfile is out of sync, orphaned or
   not additive, and the end-to-end tests run `npm ci` and `npm run check` for every configuration.

## Release a version

1. Decide the number: patch for fixes, minor for new features (and, in 0.x, for anything breaking),
   major once stable. Breaking releases need a matching `developer-platform` release (its profile
   registry declares the supported range).
2. Update `CHANGELOG.md` (move "Unreleased" under the new version).
3. `uv run pytest` must pass in full, and `uv run python tools/compare_release.py <previous tag>`
   must report that the default configuration is identical to the previous release.
4. Commit, then tag the release commit with an annotated tag and push both:

   ```bash
   git commit -m "chore: release v0.1.0"
   git tag -a v0.1.0 -m "web-core-template v0.1.0"
   git push origin main v0.1.0
   ```

5. Never move or delete a published tag; fix forward with a new patch release.

Generated projects record the tag in `.copier-answers.yml` (`_commit`) and the repository URL
(`_src_path`), which is what `copier update` uses later.
