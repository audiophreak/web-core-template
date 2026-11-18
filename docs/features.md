# Optional features

Optional features are how one template produces several kinds of project. This page is the
mechanics; the reasoning, the alternatives and the roadmap are in the `developer-platform`
repository (`docs/feature-composition.md`, `docs/full-stack-plan.md`).

Currently available: **`zod`** (Zod schema validation), **`forms`** (React Hook Form with the Zod
resolver), **`rest_api`** (versioned route handlers) and **`database`** (PostgreSQL, locally through Docker
or Supabase); the last three require `zod`. Everything else is
planned there.

## The model

A feature is:

1. a **boolean Copier question** in `copier.yml`, named like the feature, default `false`;
2. an **entry in `features.yml`**: summary, `requires`, `conflicts`, and `npm: true` if it changes
   `package.json`;
3. its **files and blocks** in `template/`, rendered only when the question is true;
4. **tests that ship in the generated project** and run inside `npm run check`.

Selecting a feature:

```bash
copier copy --data zod=true ...            # raw Copier: booleans are strict (see Booleans)
uv run devkit new my-app --set zod=true    # through devkit, which parses booleans strictly
```

The answer is recorded in the project's `.copier-answers.yml` (`zod: true`). Copier reuses recorded
answers on every `copier update`, so a project keeps its features across template releases;
features added by a newer release take their default (off). To switch one on later:
`copier update --vcs-ref <release> --data zod=true` (tested: it adds the files, the dependency and
the matching lockfile). Nothing computed is recorded.

Generation never runs a command: no Copier tasks, migrations or Jinja extensions, and no `npm
install`. Everything a configuration needs is in the template repository.

## What a feature may contain

- **Dependency lines** in `package.json.jinja`, guarded by `{% if feature %}`, with exact versions
  (the template's supply-chain policy: exact pins, releases at least two weeks old).
- **Files** in a directory whose *name* renders empty when the feature is off, for example
  `template/src/lib/{% if zod %}schemas{% endif %}/`. Copier skips a path whose name renders empty
  (with everything under it), so a disabled feature leaves no files and no empty directories. Files
  that need no substitution keep no `.jinja` suffix and are copied verbatim.
- **Small conditional blocks** in shared files (`README.md.jinja`, `AGENTS.md.jinja`, config).
  Whitespace is part of the contract: with every feature off the output must be byte-identical to
  the previous release; `tools/compare_release.py <tag>` checks it.
- **Tests and documentation** for the feature, in the generated project.

## Rules between features

`features.yml` states them; `copier.yml` enforces them at generation, for every front-end:

```yaml
# features.yml
forms:
  summary: React Hook Form with Zod resolvers
  requires: [zod]
  npm: true
```

```yaml
# copier.yml: questions are asked in order, so a prerequisite must come before what needs it
forms:
  type: bool
  default: false
  validator: >-
    {% if forms and not zod %}forms requires zod.{% endif %}
```

`tools/features.py` turns the registry into the **supported matrix** (every selection that
satisfies the rules). Tests enumerate it: every valid configuration is rendered and must have
the right dependencies and lockfile, a test checks each feature's rules are mirrored in
`copier.yml`, and synthetic registries cover requires, conflicts, chains, cycles and unknown
references. Invalid selections are rejected by the validators before anything is written.

## Lockfiles: one per dependency set

A lockfile cannot be composed from pieces, and generation may not resolve dependencies. So the
repository commits **one complete lockfile per distinct set of npm dependencies**:

```
locks/base.json     the reference lock (no npm-affecting feature)
locks/zod.json      base plus zod
locks/forms+zod.json  base plus zod plus forms (forms requires zod, so there is no forms-only lock)
locks/database-postgres+zod.json, database-supabase+zod.json, and each with forms
```

- `template/package-lock.json.jinja` is a one-line shim that includes `locks/<key>.json`. `lock_key`
  is a computed question in `copier.yml` (never asked, never recorded). Copier's Jinja search path is
  the repository root, which is why `locks/` sits outside `template/` and is never rendered itself.
- The key names only features marked `npm: true`, sorted and joined with `+` (a choice feature contributes
  `id-value`, because two providers can need different packages), so a feature that adds
  no dependency (for example Docker services) shares the base lock. The key is computed in
  `copier.yml` and in `tools/features.py`; a test renders every configuration and checks they agree.
- `uv run python tools/refresh_locks.py` writes or refreshes the lockfiles (`--check` changes
  nothing and exits 1 if one would change). It starts from the **base lock** and lets npm add only
  what the extra dependencies need, with `--before` set to two weeks ago so new transitive packages
  respect the age rule. It refuses to write a lock that is not strictly additive (see the next list).
- Tests verify, for every lockfile: the project name is templated in exactly two places; it matches
  the generated `package.json`; every package comes from the npm registry with an integrity hash
  (bundled packages are covered by their parent's); a feature lock keeps every base package version
  with the same source and hash and every direct dependency at the same path; **no new install
  scripts**; and there are no orphan lockfiles. "Additive" is judged by package identity (name,
  version, source, integrity), not by folder: npm may re-hoist an existing version into a nested
  folder when new packages change which copy is the most widely used (`forms+zod` moves six such
  locations: `ajv` 6.15.0 and 8.20.0 swap places, both stay). That is reported by the tool and does
  not change which versions are installed; a changed, removed or moved *direct* dependency fails.
- The `zod` case is instructive: Zod is already in the base lock as a development-only transitive
  dependency of the shadcn CLI, at the same version, so enabling the feature only adds it to the
  project's own dependencies and promotes the entry to a production dependency.

### Limits of the lockfile matrix

Lockfiles needed = distinct npm dependency sets, not 2^features, and each is about 400 KB. A test
fails if the matrix exceeds 16. Before that point, stop allowing every combination and declare a
short list of supported presets in `features.yml` instead; the tooling then enumerates the
presets, not all subsets. Features that need their own toolchain or cadence (a native iOS client)
belong in a separate add-on template, not in this matrix (see the platform's
`docs/feature-composition.md`).

## Adding a feature: checklist

1. Add the question to `copier.yml` in the strict boolean form (`type: yaml`, `choices: [false,
   true]`, the `is boolean` validator, default false, `help`), after its prerequisites, with a
   validator for each rule; add the entry to `features.yml`.
2. Add the dependency line(s) to `package.json.jinja` (exact versions, at least two weeks old; check
   the license and that the package has no install script).
3. Add files under conditionally named directories; add conditional blocks to shared files.
4. If the feature is `npm: true`, extend the `lock_key` default in `copier.yml` to name it, then run
   `uv run python tools/refresh_locks.py` and review `git diff --stat locks/`.
5. Write generated tests that show acceptance and rejection, and a render test file like
   `tests/test_zod_feature.py`.
6. Format the new generated files with the project's own Prettier (`npm ci` in a generated
   project, `npx prettier --write`), then copy them back.
7. Run `uv run python tools/compare_release.py <previous tag>`: with every feature off the
   project must be byte-identical to that release (apart from `.copier-answers.yml`). Then run
   `uv run pytest` and update `CHANGELOG.md` and these docs.
8. Declare the option in the platform's profile registry when releasing (`devkit --set`).

## Booleans are strict

Copier casts a `type: bool` answer **before** validators run, so a validator can never see
`banana`, and `--data zod=banana` used to enable a feature silently. Every boolean question
therefore uses this form instead:

```yaml
zod:
  type: yaml
  default: false
  choices: [false, true]
  validator: >-
    {% if zod is not boolean %}Answer true or false.{% endif %}
```

A `yaml` answer keeps its parsed type, so the validator rejects anything that is not a YAML
boolean, and `choices` gives interactive users a select list. Accepted: `true`/`false`, `yes`/`no`,
`on`/`off` in lower, Title or UPPER case. Rejected before anything is written: `banana`, `1`, `0`,
`y`, `tRuE`, an empty value, null. Answers are recorded as real booleans, so projects generated with
the earlier `type: bool` questions keep working (tested). The rule lives in `copier.yml` only, so it
holds for raw `copier` and for `devkit` alike; `tests/test_answer_types.py` enforces the form for
every boolean question and exercises both the Python API and the real command line.

## Hazards

- **Booleans must use the strict form** (next section). A plain `type: bool` question would let
  `--data zod=banana` enable the feature silently.
- **Generated files must pass the project's own Prettier**, or `npm run check` fails for every
  user of the feature.
- **The default output is a contract.** Jinja whitespace mistakes silently change existing
  projects' files on their next update; `tools/compare_release.py` catches them.
- **Plain `copier update` takes the newest tag**, even a release outside what your tooling
  supports; always pass `--vcs-ref`.

## The `forms` feature

`forms: true` requires `zod: true`; the validator in `copier.yml` rejects `forms=true` with zod off
(`forms requires zod`) before anything is written, with raw Copier and `devkit` alike, and it never
turns Zod on for you.

| What | Where |
| ---- | ----- |
| Shared schema (the rules, written once) | `src/lib/schemas/example-form.ts`, exported from `src/lib/schemas/index.ts`; tests in `example-form.test.ts` |
| Example form (client component) | `src/components/forms/example-form.tsx`; DOM tests next to it |
| Example page | `/forms-example` (`src/app/forms-example/`); delete it once the pattern is copied |
| Dependencies | production `react-hook-form` 7.91.0, `@hookform/resolvers` 5.9.2 (and its `@standard-schema/utils`); development `@testing-library/react` 16.3.3, `@testing-library/dom` 10.4.2, `jsdom` 30.1.3 |

Choices and how they were reviewed: every version is exact, at least two weeks old when chosen, MIT
licensed, and the 47 packages the lock adds over `zod` carry only permissive licenses and **no install
scripts** (a test checks both). `@hookform/resolvers` stays on 5.x, the last release of the line that
supports Zod 4 before two newer majors that were days old; revisit when 7.x has settled. The DOM
environment is selected per test file with `// @vitest-environment jsdom`, so `vitest.config.mts`
is identical with and without the feature. Tests use `fireEvent` and plain DOM assertions (no
`user-event` or `jest-dom`) to keep the added tree small.

The example is deliberately inert: no persistence, e-mail, authentication, Server Actions or API
routes, and it says on the page that nothing is saved or sent. A test fails if the component uses
`fetch`, `XMLHttpRequest`, `sendBeacon`, storage or a Server Action, and the DOM tests assert that
submission makes no request and does not navigate. Server code must still parse untrusted input with
the same schema.

Enabling it later: `copier update --vcs-ref <release> --data forms=true` on a project that already has
`zod` (tested: files, dependencies and the matching lock are added; user files are kept). On a project
without Zod the update is refused and nothing changes.

## The `rest_api` feature

`rest_api: true` requires `zod: true`, like `forms`, and is independent of `forms`: all five valid
selections (none, `zod`, `zod`+`forms`, `zod`+`rest_api`, all three) render and pass their gate, each with
Cache Components on and off. It adds **no npm dependency**: it uses the platform's `Request`,
`Response` and `ReadableStream` and the existing exact-pinned `zod`, so a project with it uses the same
lockfile as the one without (`zod` or `forms+zod`) and `lock_key` is unchanged.

| What | Where |
| ---- | ----- |
| Route handlers | `src/app/api/v1/app-info/`, `src/app/api/v1/examples/validate/`, and `src/app/api/v1/[...path]/` (the standard 404 for unknown paths) |
| Versioned contracts: request and response schemas, error codes | `src/lib/schemas/v1/`, exported as `v1` from `src/lib/schemas/index.ts` |
| Server-only helpers: `problem()`, `json()`, `readJson()`, `guarded()` | `src/server/api/` |
| Contract and boundary tests | `src/lib/schemas/v1/contract.test.ts`, `src/server/boundary.test.ts` |
| Endpoint, error and versioning documentation (shipped to the project) | `docs/api-v1.md` |

Design points, each enforced by a test in the generated project or in this repository:

- **Every boundary parses.** Request bodies are read with a size bound that holds while streaming,
  checked for media type, UTF-8 and JSON, then `safeParse`d with the versioned schema. Responses are
  validated against an explicit strict schema before they are sent; a mismatch becomes a generic 500,
  never the data. Errors are RFC 9457 `application/problem+json` with a stable `code`, no stack trace,
  path or secret, and never the submitted values (an unexpected field is reported without its name).
- **Additive within v1.** `contract.test.ts` pins every field name and error code. A breaking change
  creates `/api/v2` beside `v1`. The schemas are plain Zod, so JSON Schema or OpenAPI can be derived
  later (`z.toJSONSchema` is tested); no generator ships yet.
- **Server code is separated by directory and checked.** Only `src/server/` and `src/app/api/` may
  import `src/server/`, no client component may reach it (the check walks the import graph), and the
  shared schemas import nothing from the framework, `node:` or the environment. The roadmap's
  `import "server-only"` is not used because that package is not in the lockfile and the feature adds no
  dependency; the import-graph test enforces the same boundary.
- **No CORS headers**, no Server Actions, no database, authentication or logging of requests.

The question is an ordinary strict boolean, named like the other features (`rest_api`, a valid Jinja
identifier), with the same kind of validator as `forms`: `rest_api requires zod` is reported before
anything is written, in every flow. Its URL paths are unaffected: the API is at `/api/v1/...`.

## Choice features, and the `database` feature

A feature is usually a boolean question. A feature with providers is **one validated choice** instead of
mutually incompatible booleans: `database` is `none` (the default, which generates nothing), `postgres` or
`supabase`. In `features.yml` it lists `choices: [postgres, supabase]`; the default `none` is implicit.
`tools/features.py` models a selection as a set of tokens, `forms` for a boolean and `database=postgres`
for a choice, and `requires` and `conflicts` apply to the feature whichever value is chosen. The Copier
question is `type: str` with `choices: [none, postgres, supabase]` and a validator that rejects a database
without `zod` before anything is written. Boolean features are unchanged.

Valid combinations: `database` needs `zod` and is independent of `forms`, `rest_api` and
`cache_components`. Together with them there are thirteen supported configurations and seven lockfiles
(`rest_api` adds no dependency, so it shares the lock of the configuration without it).

| What | postgres | supabase |
| ---- | -------- | -------- |
| Local service | `compose.yaml`: PostgreSQL 17.10 pinned by its multi-arch index digest, a named volume, a health check, port on `127.0.0.1` only | `supabase/config.toml`: the Supabase CLI's local stack, PostgreSQL 17, only the database and the Data API gateway |
| Credentials | `npm run db:init` writes a random password to the git-ignored `.env.local` | the CLI's documented local defaults |
| Migrations | the runner, bookkeeping in `public.schema_migrations` with checksums | the CLI, bookkeeping in `supabase_migrations.schema_migrations` |
| Extra dependency | none | `supabase` 2.122.1 (development only) |
| Application code | identical | identical |

Everything provider-specific is the Compose file or the Supabase config, `scripts/db/cli.mjs` (one constant
selects the branch) and `.env.example`. `src/` and the migrations are byte-identical for both, which a test
checks, so a project's domain code never depends on the provider.

### Dependencies and how they were reviewed

`pg` 8.23.1, `server-only` 0.0.1 and, in development, `@types/pg` 8.23.1 (and `supabase` 2.122.1 for the
Supabase provider). All are exact, at least two weeks old when chosen, and MIT or ISC licensed, and no
package in either lock has an install script. PostgreSQL adds 16 packages to the `zod` lock; Supabase adds
31, most of them the CLI and its optional per-platform binaries, which npm installs for the current
platform only and which the lock pins with integrity hashes. The Supabase CLI is installed this way (a
pinned development dependency, its documented npm route) rather than globally, so no workstation
configuration changes. **No ORM**: parameterized SQL and a small repository meet the need.

The PostgreSQL image follows the same age rule as the packages: `postgres:17.10` was the newest 17.x release
that was two weeks old when chosen. To refresh it, pick a newer tag that is at least two weeks old, read its
digest with `docker buildx imagetools inspect postgres:<tag>`, change it in `template/compose.yaml` and
`template/.github/workflows/ci.yml.jinja` (a test checks they agree), and run the database tests.

### Migrations

`supabase/migrations/<14-digit version>_<name>.sql` is the one canonical source, named as the Supabase CLI
expects so that both providers apply the same files. `supabase/migrations.lock/` holds one `.sha256` file per
migration; one file each means a template upgrade that adds a migration and a project that adds its own
never conflict. The runner applies files in version order in separate transactions, refuses an edited,
missing or out-of-order migration, rolls a failing one back, and refuses a non-local database.

### The data-access layer and the server boundary

`src/server/db/` (configuration, pool, transactions, errors) and `src/server/items/` (the example
repository) start with `import "server-only"`, so the production build refuses to bundle them into client
code. The `server-only` package is a dependency of this feature (the REST API feature has no dependencies
and uses a directory and import-graph test instead). An import-graph test is kept as a second layer, and a
third test fails if a route handler imports the data layer: **no shipped route uses the database,**
because authentication does not exist yet. The sample table lives in schema `app`, which no HTTP API serves.

### What the Supabase provider does and does not claim

It is a working local stack, verified against a running one: the canonical migration applies, the repository
tests pass against its database, the Data API refuses the protected schema even for a forged
`service_role` token, API roles hold no privilege, and the schema equals standalone PostgreSQL's. It is
**not** production-authorized: Auth, row level security policies, a least-privilege runtime role and
protected API access belong to the authentication work package.

The Supabase CLI publishes its ports on **every** network interface and the local stack uses documented
default credentials. The template cannot change that, so `npm run db:up` asks Docker how the ports were
published and, if other machines could connect, stops the stack (its data is kept) and explains how to bind
Docker to loopback, unless `SUPABASE_ALLOW_LAN_EXPOSURE=true` is set.

### Changing provider is not an upgrade

Enabling a database on an existing project with `copier update --data database=postgres` adds files,
dependencies and the matching lock and keeps your work. **Changing an established provider** swaps the
tooling underneath the project (a test pins this) while the old database's data stays where it was, no
longer managed by the project, and moving data between databases is not automated. A Copier validator
cannot see the previous answer, so the template cannot refuse it; `devkit upgrade` does. Generate a new
project with the other provider and move the data deliberately.
