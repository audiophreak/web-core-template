# Changelog

Versions are git tags (`vMAJOR.MINOR.PATCH`). In 0.x, a minor bump may be breaking.

## Unreleased

- Generated projects no longer let Prettier check Copier's `.copier-answers.yml` (root file only, via
  `.prettierignore`). When the template is rendered from an untagged commit, Copier records the
  abbreviated hash as `_commit`; an all-digit hash is serialized single-quoted, which Prettier rejects,
  so `npm run check` failed intermittently. Released tags record `vX.Y.Z` and were not affected. The
  answers file, its serialization and every other source file are unchanged and still checked.
- The end-to-end tests record each generated app's gate output and audit before asserting the verdict, so
  a failing gate keeps its evidence. Deterministic tests cover a numeric `_commit` (`1234567`).

- CI now uploads downloadable test evidence for every run and Python version, including failed runs:
  JUnit XML, the console record, run metadata tied to the commit under test, installed package
  versions, and for each generated app in the end-to-end tests its gate output and production
  `npm audit` result (`docs/ci-evidence.md`). The exit code is unchanged. The token-shaped-string scan is quiet (matches are never printed) and gates both the job summary and the upload. Template output is
  unchanged; no release is implied.

## 0.2.0

Optional features, starting with Zod. Additive: with every feature off, the output is byte-identical
to 0.1.0 apart from `.copier-answers.yml` (which gains `zod: false`).

- New question `zod` (default `false`): adds the exact-pinned `zod` 4.6.7 dependency, a shared
  schema module in `src/lib/schemas/` (`appInfoSchema`, exported from `index.ts`) with tests for
  accepted and rejected input, and README and `AGENTS.md` guidance.
- Lockfiles are now selected per dependency set from `locks/` (`base.json`, `zod.json`) by a computed
  `lock_key`; no dependency resolution happens at generation. `features.yml` and `tools/` describe
  the supported matrix and regenerate the feature lockfiles from the base lock.
- `package-lock.json` for the default configuration is unchanged.
- Documentation: `docs/features.md`; upgrading notes for feature selections.
- Boolean questions (`zod`, and `cache_components`, which was `type: bool`) are now strict: `type: yaml`
  with `choices: [false, true]` and an `is boolean` validator. Raw Copier used to accept `--data
  zod=banana` as true because it casts a `bool` before validators run; now `banana`, `1`, `y`, an empty
  value and null are rejected before anything is written. Answers are still recorded as booleans, so
  projects generated with 0.1.0 upgrade cleanly (tested).

## 0.1.0 (first release)

Initial release, derived from the reference Web Core baseline.

- Next.js 16, React 19, strict TypeScript, Tailwind CSS v4, shadcn/ui (Radix, Lucide, neutral
  theme, `Button` as the reference component), ESLint, Prettier, Vitest.
- `npm run check` (lint, typecheck, format check, tests, production build) and a GitHub Actions
  workflow that runs `npm ci` and `npm run check`, with commit-pinned actions and read-only
  permissions.
- `AGENTS.md` and `CLAUDE.md` for AI-assisted development.
- Exact dependency versions and a committed lockfile.
- Questions: `app_name`, `app_description` (validated in `copier.yml`) and `cache_components`
  (default on; turns Next.js Cache Components with Partial Prefetching on or off).
- Generation runs no code: no `_tasks`, `_migrations` or Jinja extensions.
- CI on Linux for Python 3.12 and 3.14, including real `npm ci` and `npm run check` runs.
