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

Validation lives in `copier.yml`, so raw `copier copy` and any wrapper enforce the same rules.
Invalid answers fail before anything is written.

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
tests/            render, validation, versioned-source, upgrade and end-to-end tests
docs/             maintaining.md, upgrading.md, provenance.md
pyproject.toml    exact-pinned Copier and pytest for the test environment (uv.lock)
AGENTS.md         rules for AI agents working on this repository
```

## Tests

```bash
uv sync                        # project-local environment from uv.lock
uv run pytest                  # everything, 73 tests (about 45 s with a warm npm cache)
uv run pytest -m "not e2e"     # fast: render, validation, versions, upgrades (no npm, about 15 s)
uv run pytest -m e2e           # generates three apps and runs npm ci + npm run check in each
```
