# Security policy

## Supported versions

Security fixes are made for the latest template release (the newest `vMAJOR.MINOR.PATCH` tag) only.
Generated projects receive fixes by updating to that release (`docs/upgrading.md`).

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub: **Security › Report a vulnerability**
(https://github.com/audiophreak/web-core-template/security/advisories/new). Do not open a public issue
for a suspected vulnerability.

Include the template release, the answers you generated with (`.copier-answers.yml`, without secrets),
your Node.js version and the smallest reproduction you can. Do not include tokens, credentials, `.env`
files or database URLs with passwords. This is an independently maintained project: expect an
acknowledgement within about a week. Fixes are released as soon as practical, with credit if you would
like it.

Vulnerabilities in a dependency (Next.js, React, Better Auth, ...) should also be reported upstream; the
template ships fixes by updating its exact pins and lockfiles.

## Security posture

- Generated projects contain no secrets, credentials, personal identities or home-directory paths;
  tests scan every generated file. Local database credentials are either generated per project into the
  git-ignored, owner-only `.env.local` or are the database tool's documented local defaults; none are
  committed.
- Every Copier answer is untrusted: it is validated in `copier.yml` and only rendered through safe
  filters. The template uses no Copier `_tasks`, `_migrations` or `_jinja_extensions`, so it never
  needs `--trust`.
- npm dependencies are exact-pinned, at least two weeks old, with committed lockfiles per
  configuration and no install scripts added.
- The optional `auth` feature (Better Auth) uses a least-privilege runtime database role and
  owner-scoped data access.
- This repository's CI and the generated projects' CI use read-only `GITHUB_TOKEN` permissions,
  `persist-credentials: false`, actions pinned to full commit SHAs, store no secrets and do not run
  on `pull_request_target`.
