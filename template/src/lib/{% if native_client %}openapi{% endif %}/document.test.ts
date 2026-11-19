import {
  mkdirSync,
  readdirSync,
  readFileSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { format, resolveConfig } from "prettier";
import { describe, expect, it } from "vitest";
import { listItemsQuerySchema } from "@/lib/schemas/v1";
import {
  buildOpenApiDocument,
  LIST_QUERY_BOUNDS,
  serializeOpenApiDocument,
} from "./document";

const ROOT = process.cwd();
const COMMITTED = join(ROOT, "openapi", "openapi.json");
const ROUTES = join(ROOT, "src", "app", "api", "v1");
const METHODS = ["get", "post", "put", "patch", "delete"] as const;

/** The committed form: the generated JSON as Prettier writes it, so `npm run format:check` agrees. */
async function committedForm(): Promise<string> {
  const options = await resolveConfig(COMMITTED);
  return format(serializeOpenApiDocument(), { ...options, parser: "json" });
}

// `npm run openapi:generate` sets this; nothing else ever writes the file.
if (process.env.OPENAPI_WRITE === "1") {
  mkdirSync(dirname(COMMITTED), { recursive: true });
  writeFileSync(COMMITTED, await committedForm());
}

type Operation = { security?: unknown[]; responses: Record<string, unknown> };
const document = buildOpenApiDocument() as {
  openapi: string;
  paths: Record<string, Record<string, Operation | unknown>>;
  components: {
    schemas: Record<string, unknown>;
    responses: Record<string, unknown>;
    securitySchemes: Record<string, unknown>;
  };
};

function routeFiles(directory: string): string[] {
  return readdirSync(directory).flatMap((name) => {
    const path = join(directory, name);
    if (statSync(path).isDirectory()) return routeFiles(path);
    return name === "route.ts" ? [path] : [];
  });
}

/** `[id]` is `{id}`; the catch-all `[...path]` (the 404 for unknown paths) is not an operation. */
const urlOf = (file: string) =>
  `/api/v1/${file
    .slice(ROUTES.length + 1, -"/route.ts".length)
    .replace(/\[(\w+)\]/g, "{$1}")}`;

function implemented() {
  const found = new Map<string, { methods: string[]; protected: boolean }>();
  for (const file of routeFiles(ROUTES)) {
    if (file.includes("[...")) continue;
    const source = readFileSync(file, "utf8");
    const methods = [
      ...source.matchAll(
        /export (?:const|function) (GET|POST|PUT|PATCH|DELETE)\b\s*(=\s*(\w+))?/g,
      ),
    ]
      .filter((match) => match[3] !== "notAllowed")
      .map((match) => match[1].toLowerCase());
    found.set(urlOf(file), {
      methods: methods.sort(),
      protected: /withAuthentication|sessionFromHeaders/.test(source),
    });
  }
  return found;
}

describe("openapi/openapi.json", () => {
  it("is exactly what the Zod schemas and the endpoint list generate (run `npm run openapi:generate` after a change)", async () => {
    expect(readFileSync(COMMITTED, "utf8")).toBe(await committedForm());
  });

  it("is OpenAPI 3.1 and generating twice gives the same bytes", () => {
    expect(document.openapi).toBe("3.1.0");
    expect(serializeOpenApiDocument()).toBe(serializeOpenApiDocument());
  });
});

describe("the contract and the routes agree", () => {
  const routes = implemented();

  it("documents every implemented operation and nothing else", () => {
    const documented = Object.fromEntries(
      Object.entries(document.paths).map(([path, item]) => [
        path,
        METHODS.filter((method) => method in item).sort(),
      ]),
    );
    const actual = Object.fromEntries(
      [...routes].map(([path, r]) => [path, r.methods]),
    );
    expect(documented).toEqual(actual);
  });

  it("requires the bearer scheme exactly where the handler requires a session", () => {
    for (const [path, route] of routes) {
      for (const method of route.methods) {
        const operation = (document.paths[path] as Record<string, Operation>)[
          method
        ];
        expect(operation.security, `${method} ${path}`).toEqual(
          route.protected ? [{ bearerAuth: [] }] : [],
        );
        const has401 = "401" in operation.responses;
        expect(has401, `${method} ${path} 401`).toBe(route.protected);
      }
    }
  });

  it("describes bearer as an http scheme and nothing that uses a cookie", () => {
    expect(document.components.securitySchemes).toEqual({
      bearerAuth: expect.objectContaining({ type: "http", scheme: "bearer" }),
    });
  });

  it("resolves every reference", () => {
    const text = JSON.stringify(document);
    for (const [, name] of text.matchAll(/#\/components\/schemas\/(\w+)/g)) {
      expect(document.components.schemas, name).toHaveProperty(name);
    }
    for (const [, name] of text.matchAll(/#\/components\/responses\/(\w+)/g)) {
      expect(document.components.responses, name).toHaveProperty(name);
    }
  });

  it("documents the list bounds the route's schema enforces", () => {
    const { limit, offset } = LIST_QUERY_BOUNDS;
    const parse = (query: Record<string, string>) =>
      listItemsQuerySchema.safeParse(query);
    expect(parse({}).data).toEqual({
      limit: limit.default,
      offset: offset.default,
    });
    expect(parse({ limit: String(limit.minimum) }).success).toBe(true);
    expect(parse({ limit: String(limit.maximum) }).success).toBe(true);
    expect(parse({ limit: String(limit.minimum - 1) }).success).toBe(false);
    expect(parse({ limit: String(limit.maximum + 1) }).success).toBe(false);
    expect(parse({ offset: String(offset.maximum) }).success).toBe(true);
    expect(parse({ offset: String(offset.maximum + 1) }).success).toBe(false);
  });

  it("never describes a field that would carry a token, a password or an owner id", () => {
    expect(JSON.stringify(document.components.schemas)).not.toMatch(
      /token|password|owner/i,
    );
  });
});
