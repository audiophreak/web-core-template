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

Notes:

- Conflicts are written inline (`<<<<<<<`, `=======`, `>>>>>>>`) and the command still exits 0:
  search for the markers before committing. Nothing is silently lost (tests cover this).
- `package.json` and the lockfile conflict most often. Resolve `package.json` first, then run
  `npm install` instead of merging the lockfile by hand.
- Questions added after your project was generated take their defaults; override one with
  `--data NAME=VALUE`.
- If the template moves, edit `_src_path` in `.copier-answers.yml` to the new URL.
