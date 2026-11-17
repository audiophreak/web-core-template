# Changelog

Versions are git tags (`vMAJOR.MINOR.PATCH`). In 0.x, a minor bump may be breaking.

## 0.2.0 (unreleased)

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
