# Changelog

Versions are git tags (`vMAJOR.MINOR.PATCH`). In 0.x, a minor bump may be breaking.

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
