# API v1

The application's JSON API. Every path starts with `/api/v1`; the version is part of the path.

## Endpoints

| Method | Path                            | Request body             | Success                        | Errors                  |
| ------ | ------------------------------- | ------------------------ | ------------------------------ | ----------------------- |
| GET    | `/api/v1/app-info`              | none                     | 200, `AppInfo`                 | 405                     |
| POST   | `/api/v1/examples/validate`     | `ValidateExampleRequest` | 200, `ValidateExampleResponse` | 400, 405, 413, 415, 422 |
| any    | any other path under `/api/v1/` | -                        | -                              | 404                     |

Other methods on a known path return 405 with an `Allow` header. Responses carry no CORS headers: the
API answers same-origin callers only until an origin policy is chosen on purpose.

### GET /api/v1/app-info

```json
{ "name": "my-app", "description": "What it does" }
```

### POST /api/v1/examples/validate

An example of the validation boundary: it checks the body and reports what it accepted, without
storing anything, contacting anything or repeating the submitted text.

```json
{ "title": "Write the report", "priority": "high", "tags": ["work", "q4"] }
```

| Field      | Type                          | Rules                                                                                                      |
| ---------- | ----------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `title`    | string, required              | trimmed, 1 to 100 characters                                                                               |
| `priority` | `"low" \| "normal" \| "high"` | optional, default `"normal"`                                                                               |
| `tags`     | array of strings              | optional, default `[]`, at most 5; each 1 to 20 characters of lowercase letters, digits and single hyphens |

Unknown fields are rejected. The body must be `application/json` and at most 8 KiB. Result:

```json
{ "valid": true, "titleLength": 16, "priority": "high", "tagCount": 2 }
```

## Errors

Every error is an RFC 9457 problem document with the media type `application/problem+json`:

```json
{
  "type": "urn:problem-type:validation_failed",
  "title": "The request is not valid",
  "status": 422,
  "code": "validation_failed",
  "errors": [{ "path": "title", "message": "Title is required" }]
}
```

Branch on `code`, never on `title`. `errors` appears only for `validation_failed` (at most 10 entries;
`path` is dot-separated, empty for the whole body). A problem never contains a stack trace, a file
path, a secret or the submitted values.

| Status | `code`                   | Meaning                                                |
| ------ | ------------------------ | ------------------------------------------------------ |
| 400    | `invalid_json`           | The body is empty, not valid UTF-8 or not valid JSON   |
| 404    | `not_found`              | No such resource under `/api/v1/`                      |
| 405    | `method_not_allowed`     | The method is not supported here (see `Allow`)         |
| 413    | `payload_too_large`      | The body is over the limit                             |
| 415    | `unsupported_media_type` | The body is not `application/json` (see `Accept-Post`) |
| 422    | `validation_failed`      | The JSON is well-formed but breaks the schema          |
| 500    | `internal_error`         | An unexpected failure; nothing about it is revealed    |

## Versioning and compatibility

- Within `v1`, changes are **additive only**: new endpoints, new optional request fields, new response
  fields and new error codes. Nothing is removed, renamed, made required or tightened.
- Clients must **ignore response fields and error codes they do not know**. The server's own response
  schemas are strict so it never sends a field it did not declare; client code should be lenient.
- A breaking change creates `/api/v2` beside `v1`; `v1` is retired only after a published
  deprecation window, because native apps cannot be forced to update.
- `src/lib/schemas/v1/contract.test.ts` pins every field name and error code, so a breaking change
  fails the test suite instead of reaching a client.

## Where things are

| What                                       | Where                 |
| ------------------------------------------ | --------------------- |
| Request and response schemas, error codes  | `src/lib/schemas/v1/` |
| Route handlers                             | `src/app/api/v1/`     |
| Response and request helpers (server only) | `src/server/api/`     |

The schemas are plain Zod and JSON-Schema compatible (`z.toJSONSchema`), so an OpenAPI document or a
native client can be generated from them later. Add a new endpoint by writing its schemas in
`src/lib/schemas/v1/`, its test, then its handler: read with `readJson`, `safeParse`, answer with
`json(schema, data)` or `problem(code)`.
