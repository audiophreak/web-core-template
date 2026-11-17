# Provenance

`template/` was derived from the reference Web Core application at its merged shadcn/ui baseline
(commit `f244594`, merged to `main` as `bf3643c`). It was verified by rendering the template with
the reference's app name and comparing every file against that commit.

## Result: 16 of 22 shared files are byte-identical; the rest differ intentionally

| File | Difference from the reference |
| ---- | ----------------------------- |
| `package.json` | Adds a `description`; the six `^` ranges inherited from `create-next-app` (`@tailwindcss/turbopack`, `@types/react`, `@types/react-dom`, `eslint`, `tailwindcss`, `typescript`) become exact pins at the versions the reference lockfile already resolved: 4.3.5, 19.3.0, 19.3.0, 9.39.5, 4.3.5, 5.9.3. |
| `package-lock.json` | All 719 package entries identical. Only the project name and the six root specifiers above differ. |
| `README.md` | Reworded for a generated project (name, description, `fnm` hint, CI and template-update notes). |
| `src/app/layout.tsx`, `src/app/page.tsx`, `src/app/page.test.tsx` | Read the app name and description from `src/lib/app-info.json` instead of the `create-next-app` marketing page and metadata. |
| `public/*.svg` (5 files) | Not carried over; only the removed marketing page used them. |
| `next.config.ts` | Identical output with `cache_components` on (the default); the flags are omitted when it is off. |
| Added | `.copier-answers.yml`, `.github/workflows/ci.yml`, `src/lib/app-info.json`. |

Everything else (`.gitignore`, `.nvmrc`, `.prettierrc.json`, `.prettierignore`, `AGENTS.md`,
`CLAUDE.md`, `components.json`, `eslint.config.mjs`, `tsconfig.json`, `vitest.config.mts`,
`src/app/globals.css`, `src/app/favicon.ico`, `src/components/ui/button*.tsx`, `src/lib/utils.ts`)
is identical.

To repeat the comparison: render with `--data app_name=<reference name>` and diff against
`git archive <commit>` of the reference.
