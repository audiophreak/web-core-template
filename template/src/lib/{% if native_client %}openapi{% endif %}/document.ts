import { z } from "zod";
import {
  appInfoResponseSchema,
  createItemRequestSchema,
  itemListResponseSchema,
  itemResponseSchema,
  problemCodes,
  problemDetailsSchema,
  sessionListResponseSchema,
  sessionResponseSchema,
  updateItemRequestSchema,
  validateExampleRequestSchema,
  validateExampleResponseSchema,
} from "@/lib/schemas/v1";
import { itemIdSchema } from "@/lib/schemas";

/**
 * The OpenAPI 3.1 contract of the application-owned API (`/api/v1`), built from the Zod schemas that
 * the route handlers validate with. `openapi/openapi.json` is this document, committed; the test next
 * to this file fails when the two differ, when a route is missing here or when this lists a route that
 * does not exist. Regenerate with `npm run openapi:generate`, review the diff, commit it.
 *
 * Not described here, on purpose: Better Auth's own endpoints under `/api/auth` (sign-up, sign-in,
 * sign-out). They belong to the library and are documented in docs/native-client.md; the contract
 * is the API this application owns. The bearer token a client sends to the endpoints below is the
 * `set-auth-token` response header of Better Auth's sign-in.
 */

type Json = Record<string, unknown>;

/** Which status each stable problem code is sent with (a test compares this with the handlers). */
export const PROBLEM_STATUS: Record<(typeof problemCodes)[number], number> = {
  invalid_json: 400,
  unauthenticated: 401,
  origin_not_allowed: 403,
  not_found: 404,
  method_not_allowed: 405,
  payload_too_large: 413,
  unsupported_media_type: 415,
  validation_failed: 422,
  internal_error: 500,
};

const REF = "#/components/schemas/";

function toJsonSchemas(
  entries: Record<string, z.ZodType>,
  io: "input" | "output",
): Record<string, Json> {
  const registry = z.registry<{ id: string }>();
  for (const [id, schema] of Object.entries(entries))
    registry.add(schema, { id });
  const { schemas } = z.toJSONSchema(registry, {
    target: "draft-2020-12",
    io,
    uri: (id: string) => `${REF}${id}`,
  } as never) as { schemas: Record<string, Json> };
  return Object.fromEntries(
    Object.entries(schemas).map(([id, schema]) => [
      id,
      tidy(schema, io === "output"),
    ]),
  );
}

/**
 * Makes a generated JSON Schema fit OpenAPI: no `$schema` / `$id`; no `pattern` next to a `format`
 * (the format says it, and the generated expressions are long); no integer bound beyond what a
 * client can hold. In responses an object also allows fields it does not know, because within v1
 * fields are only ever added and clients must ignore them (requests stay strict).
 */
function tidy(node: unknown, response: boolean): Json {
  if (Array.isArray(node))
    return node.map((child) => tidy(child, response)) as never;
  if (node === null || typeof node !== "object") return node as never;
  const nullable = nullableForm(node);
  if (nullable) return tidy(nullable, response);
  const out: Json = {};
  for (const [key, value] of Object.entries(node)) {
    if (key === "$schema" || key === "$id") continue;
    if (key === "pattern" && "format" in node) continue;
    if (key === "maximum" && value === Number.MAX_SAFE_INTEGER) continue;
    if (key === "additionalProperties" && value === false && response) continue;
    out[key] = tidy(value, response);
  }
  // Within v1 a response may gain values (new problem codes), and a generated client with a closed enum fails
  // to decode one it does not know. So in a response the known values are listed as `examples` and the type
  // stays an open string; requests keep their closed `enum`.
  if (response && Array.isArray(out.enum) && out.type === "string") {
    out.examples = out.enum;
    delete out.enum;
  }
  return out;
}

/**
 * Zod writes `.nullable()` as `anyOf: [X, { type: "null" }]`. OpenAPI 3.1 and the Swift generator express the
 * same thing as `type: [X's type, "null"]`, which is what this returns for a simple X (otherwise nothing).
 */
function nullableForm(node: object): Json | null {
  const { anyOf, ...rest } = node as { anyOf?: Json[] } & Json;
  if (!Array.isArray(anyOf) || anyOf.length !== 2) return null;
  const nullBranch = anyOf.findIndex((branch) => branch.type === "null");
  const other = anyOf[1 - nullBranch];
  if (nullBranch < 0 || typeof other?.type !== "string") return null;
  return { ...other, ...rest, type: [other.type, "null"] };
}

const responseSchemas = toJsonSchemas(
  {
    AppInfo: appInfoResponseSchema,
    Item: itemResponseSchema,
    ItemList: itemListResponseSchema,
    Session: sessionResponseSchema,
    SessionList: sessionListResponseSchema,
    ValidateExampleResult: validateExampleResponseSchema,
    Problem: problemDetailsSchema,
  },
  "output",
);

const requestSchemas = toJsonSchemas(
  {
    CreateItemRequest: createItemRequestSchema,
    UpdateItemRequest: updateItemRequestSchema,
    ValidateExampleRequest: validateExampleRequestSchema,
  },
  "input",
);

const itemId = tidy(
  z.toJSONSchema(itemIdSchema, { target: "draft-2020-12" }),
  false,
);

const ref = (name: string): Json => ({ $ref: `${REF}${name}` });
const responseRef = (name: string): Json => ({
  $ref: `#/components/responses/${name}`,
});

const jsonBody = (name: string, required = true): Json => ({
  required,
  content: { "application/json": { schema: ref(name) } },
});

const jsonResponse = (
  description: string,
  name: string,
  headers?: Json,
): Json => ({
  description,
  ...(headers ? { headers } : {}),
  content: { "application/json": { schema: ref(name) } },
});

function problemResponse(status: number): Json {
  const codes = Object.entries(PROBLEM_STATUS)
    .filter(([, s]) => s === status)
    .map(([code]) => `\`${code}\``);
  return {
    description: `Problem details (RFC 9457). Stable \`code\`: ${codes.join(", ")}.`,
    headers: {
      "Cache-Control": {
        description: "Always `no-store`.",
        schema: { type: "string" },
      },
      ...(status === 405
        ? {
            Allow: {
              description: "The methods this resource supports.",
              schema: { type: "string" },
            },
          }
        : {}),
    },
    content: { "application/problem+json": { schema: ref("Problem") } },
  };
}

const problemStatuses = [...new Set(Object.values(PROBLEM_STATUS))].sort(
  (a, b) => a - b,
);
const statusName: Record<number, string> = {
  400: "BadRequest",
  401: "Unauthenticated",
  403: "OriginNotAllowed",
  404: "NotFound",
  405: "MethodNotAllowed",
  413: "PayloadTooLarge",
  415: "UnsupportedMediaType",
  422: "ValidationFailed",
  500: "InternalError",
};

const problems = (...statuses: number[]): Json =>
  Object.fromEntries(
    statuses.map((s) => [String(s), responseRef(statusName[s]!)]),
  );

const secured = [{ bearerAuth: [] }];
const idParameter = {
  name: "id",
  in: "path",
  required: true,
  description: "An item id.",
  schema: { $ref: "#/components/schemas/ItemId" },
};

const limitSchema = { type: "integer", minimum: 1, maximum: 100, default: 20 };
const offsetSchema = {
  type: "integer",
  minimum: 0,
  maximum: 100000,
  default: 0,
};

/** The documented bounds of the list query; the test proves the route's Zod schema agrees. */
export const LIST_QUERY_BOUNDS = {
  limit: limitSchema,
  offset: offsetSchema,
} as const;

export function buildOpenApiDocument(): Json {
  return {
    openapi: "3.1.0",
    info: {
      title: "Application API",
      version: "1.0.0",
      description:
        "The application-owned REST API, version 1. Errors are RFC 9457 problem details with a stable `code`. Within v1 fields and codes are only added, never renamed or removed; clients ignore fields they do not know. Protected operations take the Better Auth session token as `Authorization: Bearer <token>`.",
    },
    servers: [{ url: "/", description: "The application's own origin." }],
    paths: {
      "/api/v1/app-info": {
        get: {
          operationId: "getAppInfo",
          summary: "The application's name and description",
          tags: ["public"],
          security: [],
          responses: {
            "200": jsonResponse(
              "The application's name and description.",
              "AppInfo",
            ),
            ...problems(500),
          },
        },
      },
      "/api/v1/examples/validate": {
        post: {
          operationId: "validateExample",
          summary: "Validate an example request body (nothing is stored)",
          tags: ["public"],
          security: [],
          requestBody: jsonBody("ValidateExampleRequest"),
          responses: {
            "200": jsonResponse("The body is valid.", "ValidateExampleResult"),
            ...problems(400, 413, 415, 422, 500),
          },
        },
      },
      "/api/v1/session": {
        get: {
          operationId: "getSession",
          summary: "Who the caller is and when the session ends",
          tags: ["session"],
          security: secured,
          responses: {
            "200": jsonResponse(
              "The signed-in user and the session's expiry.",
              "Session",
            ),
            ...problems(401, 500),
          },
        },
      },
      "/api/v1/sessions": {
        get: {
          operationId: "listSessions",
          summary: "The caller's own sessions",
          tags: ["session"],
          security: secured,
          responses: {
            "200": jsonResponse(
              "The caller's sessions; the current one is marked.",
              "SessionList",
            ),
            ...problems(401, 500),
          },
        },
      },
      "/api/v1/items": {
        get: {
          operationId: "listItems",
          summary: "The caller's items, newest first",
          tags: ["items"],
          security: secured,
          parameters: [
            {
              name: "limit",
              in: "query",
              required: false,
              schema: limitSchema,
            },
            {
              name: "offset",
              in: "query",
              required: false,
              schema: offsetSchema,
            },
          ],
          responses: {
            "200": jsonResponse("A page of the caller's items.", "ItemList"),
            ...problems(401, 422, 500),
          },
        },
        post: {
          operationId: "createItem",
          summary: "Create an item owned by the caller",
          tags: ["items"],
          security: secured,
          requestBody: jsonBody("CreateItemRequest"),
          responses: {
            "201": jsonResponse("The created item.", "Item", {
              Location: {
                description: "The new item's URL.",
                schema: { type: "string" },
              },
            }),
            ...problems(400, 401, 403, 413, 415, 422, 500),
          },
        },
      },
      "/api/v1/items/{id}": {
        parameters: [idParameter],
        get: {
          operationId: "getItem",
          summary: "One of the caller's items",
          description:
            "Someone else's item answers 404, exactly like a missing one.",
          tags: ["items"],
          security: secured,
          responses: {
            "200": jsonResponse("The item.", "Item"),
            ...problems(401, 404, 500),
          },
        },
        patch: {
          operationId: "updateItem",
          summary: "Change the title and/or body of one of the caller's items",
          tags: ["items"],
          security: secured,
          requestBody: jsonBody("UpdateItemRequest"),
          responses: {
            "200": jsonResponse("The updated item.", "Item"),
            ...problems(400, 401, 403, 404, 413, 415, 422, 500),
          },
        },
        delete: {
          operationId: "deleteItem",
          summary: "Delete one of the caller's items",
          tags: ["items"],
          security: secured,
          responses: {
            "204": { description: "The item was deleted." },
            ...problems(401, 403, 404, 500),
          },
        },
      },
    },
    components: {
      securitySchemes: {
        bearerAuth: {
          type: "http",
          scheme: "bearer",
          description:
            "The signed session token Better Auth returns in the `set-auth-token` header of a successful sign-in (POST /api/auth/sign-in/email). It is the same session the browser cookie belongs to: it expires, and sign-out or revocation ends it at once.",
        },
      },
      schemas: { ...responseSchemas, ...requestSchemas, ItemId: itemId },
      responses: Object.fromEntries(
        problemStatuses.map((status) => [
          statusName[status]!,
          problemResponse(status),
        ]),
      ),
    },
  };
}

/** The committed form: two-space JSON with a final newline. */
export function serializeOpenApiDocument(): string {
  return `${JSON.stringify(buildOpenApiDocument(), null, 2)}\n`;
}
