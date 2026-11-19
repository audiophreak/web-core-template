# web-core-template

An independently versioned [Copier](https://copier-org.github.io/copier/) template for a Web Core
application: Next.js 16 (App Router), React 19, strict TypeScript, Tailwind CSS v4, shadcn/ui
(Radix primitives, Lucide icons), ESLint, Prettier, Vitest, a GitHub Actions workflow that runs
`npm ci` and `npm run check`, and `AGENTS.md`/`CLAUDE.md` for AI-assisted development.

Releases are git tags named `vMAJOR.MINOR.PATCH`. Each release is the unit generated projects
record and upgrade between. The command line that normally drives this template lives in a
separate repository, `developer-platform` (`devkit new`); this template does not depend on it.

## Use it

With `devkit` (picks the newest release that devkit supports):

```bash
uv run devkit new my-app --description "What it does" --output-dir ..
```

With Copier directly, from the published origin and an explicit release (the repository is
private, so git needs credentials it can use without prompting, for example after `gh auth
setup-git`):

```bash
copier copy --vcs-ref v0.1.0 https://github.com/OWNER/web-core-template.git ../my-app
```

Without `--vcs-ref`, Copier renders the newest tag. For template development use a checkout and
`--vcs-ref HEAD`: once the repository has any tag, Copier otherwise ignores uncommitted work.

## Answers

| Question           | Type | Default                 | Meaning                                                          |
| ------------------ | ---- | ----------------------- | ---------------------------------------------------------------- |
| `app_name`         | str  | required                | Lowercase letters, digits and single hyphens, 1-64, starting with a letter. The npm package name. |
| `app_description`  | str  | `A Next.js application` | One line, up to 200 ASCII letters, digits and `. , ; : ! ? ' " ( ) / & + -`, starting with a letter, single spaces. |
| `cache_components` | bool | `true`                  | Enables Next.js Cache Components with Partial Prefetching (`cacheComponents` and `partialPrefetching` in `next.config.ts`). Data fetching becomes dynamic by default and caching is opt-in. Next.js documents `partialPrefetching` as requiring `cacheComponents` and plans to make both universal in its next major release, so they are one switch here. |
| `zod`              | bool | `false`                 | Adds Zod schema validation: the exact-pinned `zod` dependency, a shared schema module in `src/lib/schemas/` (`appInfoSchema` for `src/lib/app-info.json`) with tests that show accepted and rejected input, and matching README and `AGENTS.md` guidance. Off by default; with it off the output is identical to release 0.1.0 apart from `.copier-answers.yml`. |
| `database`         | choice | `none`                | `none` (default, generates nothing), `postgres` or `supabase`. Adds a local PostgreSQL 17 database (Docker Compose, or the pinned Supabase CLI's local stack), version-controlled SQL migrations in `supabase/migrations/` (one canonical source for both providers, with a checksum lock), a typed **server-only** data-access layer (`pg`, `server-only`) with a sample `items` repository, real-database tests (`npm run test:db`), `npm run db:*` commands and `docs/database.md`. **Requires `zod=true`**; rejected before anything is written otherwise. It does not enable Forms or the REST API and adds no route that touches the database: authentication does not exist yet. Local development only. |
| `rest_api`         | bool | `false`                 | Adds the versioned REST API: route handlers under `/api/v1` (`GET /api/v1/app-info`, `POST /api/v1/examples/validate`), request and response schemas in `src/lib/schemas/v1/`, RFC 9457 problem-details errors with stable codes, server-only helpers in `src/server/`, `docs/api-v1.md` and handler tests that call handlers with `Request` objects (no server). **Requires `zod=true`**, never enables it, adds **no npm dependency** and shares the existing lockfile. |
| `auth`             | bool | `false`                 | Adds Better Auth email/password authentication on either database provider: sessions in PostgreSQL, a least-privilege `app_runtime` database role for the application, owner-scoped items protected by row level security, and a protected `/api/v1/items` API. Requires `zod`, `rest_api` and a `database`; never enables them. Local-development ready, not ready for open public registration: see the generated `docs/auth.md`. |
| `native_client`    | bool | `false`                 | Serves native (non-browser) clients such as an iOS app: accepts the Better Auth session token as `Authorization: Bearer` (the plugin's signed token, same sessions, expiry, revocation and authorization as the browser cookie) and commits a deterministic **OpenAPI 3.1** contract for `/api/v1` in `openapi/openapi.json`, generated from the Zod schemas and checked by `npm run check`. Requires `auth`; adds no dependency. See the generated `docs/native-client.md`. |
| `forms`            | bool | `false`                 | Adds React Hook Form with the Zod resolver: exact-pinned `react-hook-form` and `@hookform/resolvers`, a small accessible example form at `/forms-example` validated by a shared schema (`exampleFormSchema`), DOM-tested with `@testing-library/react` and `jsdom` (development only), and matching README and `AGENTS.md` guidance. **Requires `zod=true`**: `forms=true` without it is rejected before anything is written, and `forms` never enables Zod for you. The example saves and sends nothing. |

Validation lives in `copier.yml`, so raw `copier copy` and any wrapper enforce the same rules.
Invalid answers fail before anything is written. Features (`zod`; `forms`, `rest_api` and `database`, which require `zod`; `auth`, which requires `zod`, `rest_api` and a `database`; and `native_client`, which requires `auth`) are optional;
`docs/features.md` explains how they compose, how rules between them are enforced and how their
lockfiles are maintained. Boolean answers are strict: `true`/`false`, `yes`/`no` and `on`/`off` (in
lower, Title or UPPER case) are accepted; anything else (`banana`, `1`, `y`, an empty value) is
rejected before anything is written, with raw `copier` and with `devkit` alike.

## Versioning

Semantic versioning, with the 0.x convention that a **minor** bump may break. Breaking changes
include: a new Node.js major, a renamed or removed question, a changed answer type or default
that alters existing projects, and any change a wrapper must react to. The platform declares which
releases it supports (`developer-platform`'s profile registry), so a breaking release needs a
matching platform release. See `CHANGELOG.md`, `docs/maintaining.md` (releasing), `docs/upgrading.md`
(updating generated projects) and `docs/provenance.md` (where the template came from).

## Repository layout

```
.github/          CI: runs the full test suite on pull requests and pushes to main
copier.yml        questions, validators, exclusions (rendered from ./template)
template/         files rendered into projects; *.jinja files are templated
features.yml      registry of optional features (rules between them, which change npm dependencies)
locks/            one committed lockfile per distinct npm dependency set (never rendered itself)
tools/            features.py (the supported matrix), refresh_locks.py and compare_release.py
tests/            render, validation, versioned-source, upgrade and end-to-end tests
docs/             features.md, maintaining.md, upgrading.md, provenance.md, ci-evidence.md
pyproject.toml    exact-pinned Copier and pytest for the test environment (uv.lock)
AGENTS.md         rules for AI agents working on this repository
```

## Tests

```bash
uv sync                                   # project-local environment from uv.lock
uv run pytest -m "not e2e and not db"     # fast: render, validation, features, locks, versions, upgrades (no npm, no Docker)
uv run pytest -m e2e                      # generates apps and runs npm ci + npm run check in each (Node 26, network)
uv run pytest -m db                       # real PostgreSQL and a real local Supabase stack (a running Docker daemon)
uv run pytest                             # everything
uv run pytest --collect-only -q           # the current number of tests, per marker with -m
```

Two markers split the suite by what it needs: `e2e` (installs and checks generated apps) and `db` (starts databases in
Docker). A few tests carry both. Without Docker the `db` tests skip with a stated reason; CI sets `REQUIRE_DOCKER=1`, so
there they fail instead. CI runs `-m "not db"` on Python 3.12 and 3.14 and `-m db` in a separate `database` job.

The main end-to-end test, `test_generated_app_installs_and_passes_the_validation_gate`, is parametrized over feature
combinations (21 at 0.7.x: from the defaults through Zod, Forms and the REST API, each database provider,
authentication and native-client support, several with `cache_components` off); further e2e tests cover evidence recording and the Prettier handling of
`.copier-answers.yml`. At 0.7.x, `pytest --collect-only` reports 919 tests: 780 without either marker, 32 `e2e`, 109 `db`.
