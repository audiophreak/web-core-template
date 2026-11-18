import { afterAll, beforeAll, describe, expect, it } from "vitest";
import pg from "pg";
import {
  itemListResponseSchema,
  itemResponseSchema,
  sessionListResponseSchema,
  sessionResponseSchema,
} from "@/lib/schemas/v1";
import { expectProblem } from "@/server/api/expect-problem";
import {
  createRuntimeDatabase,
  forgetApplication,
  jsonRequest,
  requestTo,
  signUp,
  type RuntimeDatabase,
  type TestUser,
} from "@/server/auth/test-support/harness";
import { adminUrl } from "@/server/db/test-support/database";
import {
  DELETE as deleteOne,
  GET as getOne,
  PATCH as patchOne,
  POST as postOne,
} from "./[id]/route";
import { DELETE, GET, PATCH, POST, PUT } from "./route";
import { GET as getSession, POST as postSession } from "../session/route";
import { GET as getSessions } from "../sessions/route";

/**
 * The protected API through its route handlers, against a real PostgreSQL and real Better Auth sessions:
 * who gets in, what each user can reach, what the responses contain, and what happens when things go wrong.
 */
let database: RuntimeDatabase;
let alice: TestUser;
let bob: TestUser;

async function admin<T = Record<string, unknown>>(
  sql: string,
  values: unknown[] = [],
): Promise<T[]> {
  const url = new URL(adminUrl());
  url.pathname = `/${database.name}`;
  const client = new pg.Client({ connectionString: url.toString() });
  await client.connect();
  try {
    return (await client.query(sql, values)).rows as T[];
  } finally {
    await client.end();
  }
}

const withId = (id: string) => ({ params: Promise.resolve({ id }) });
const MISSING = "00000000-0000-4000-8000-000000000000";

async function create(user: TestUser, title: string, body = "") {
  const response = await POST(
    jsonRequest("/api/v1/items", "POST", { title, body }, user),
    undefined,
  );
  expect(response.status).toBe(201);
  return itemResponseSchema.parse(await response.json());
}

beforeAll(async () => {
  database = await createRuntimeDatabase();
  alice = await signUp("alice");
  bob = await signUp("bob");
});

afterAll(async () => {
  await forgetApplication();
  await database.drop();
});

describe("without a valid session", () => {
  const forged = new Headers({
    cookie: "better-auth.session_token=forged.forged",
  });
  const calls: Array<[string, (headers?: Headers) => Promise<Response>]> = [
    [
      "GET /items",
      (h) => GET(requestTo("/api/v1/items", { headers: h }), undefined),
    ],
    [
      "POST /items",
      (h) =>
        POST(
          requestTo("/api/v1/items", {
            method: "POST",
            headers: {
              "content-type": "application/json",
              ...Object.fromEntries(h ?? []),
            },
            body: JSON.stringify({ title: "no" }),
          }),
          undefined,
        ),
    ],
    [
      "GET /items/:id",
      (h) =>
        getOne(
          requestTo(`/api/v1/items/${MISSING}`, { headers: h }),
          withId(MISSING),
        ),
    ],
    [
      "PATCH /items/:id",
      (h) =>
        patchOne(
          requestTo(`/api/v1/items/${MISSING}`, {
            method: "PATCH",
            headers: {
              "content-type": "application/json",
              ...Object.fromEntries(h ?? []),
            },
            body: "{}",
          }),
          withId(MISSING),
        ),
    ],
    [
      "DELETE /items/:id",
      (h) =>
        deleteOne(
          requestTo(`/api/v1/items/${MISSING}`, {
            method: "DELETE",
            headers: h,
          }),
          withId(MISSING),
        ),
    ],
    [
      "GET /session",
      (h) => getSession(requestTo("/api/v1/session", { headers: h })),
    ],
    [
      "GET /sessions",
      (h) => getSessions(requestTo("/api/v1/sessions", { headers: h })),
    ],
  ];

  it.each(calls)(
    "answers %s with the stable 401 problem, for no cookie and for a forged one",
    async (_name, call) => {
      for (const headers of [undefined, forged]) {
        const problem = await expectProblem(
          await call(headers),
          401,
          "unauthenticated",
        );
        expect(problem.title).toBe("Authentication is required");
      }
    },
  );

  it("creates nothing when an unauthenticated POST is refused", async () => {
    await calls[1][1]();
    expect(await admin("select 1 from app.items where title = 'no'")).toEqual(
      [],
    );
  });
});

describe("the items of the signed-in caller", () => {
  it("creates, reads, updates, lists and deletes an item with explicit response schemas", async () => {
    const created = await create(alice, "First item", "hello");
    expect(Object.keys(created).sort()).toEqual([
      "body",
      "createdAt",
      "id",
      "title",
      "updatedAt",
    ]);

    const read = await getOne(
      requestTo(`/api/v1/items/${created.id}`, { user: alice }),
      withId(created.id),
    );
    expect(itemResponseSchema.parse(await read.json())).toEqual(created);

    const patched = await patchOne(
      jsonRequest(
        `/api/v1/items/${created.id}`,
        "PATCH",
        { body: "changed" },
        alice,
      ),
      withId(created.id),
    );
    expect(patched.status).toBe(200);
    expect(itemResponseSchema.parse(await patched.json()).body).toBe("changed");

    const list = await GET(
      requestTo("/api/v1/items?limit=5", { user: alice }),
      undefined,
    );
    const listed = itemListResponseSchema.parse(await list.json());
    expect(listed).toMatchObject({ limit: 5, offset: 0 });
    expect(listed.items.map((item) => item.id)).toContain(created.id);

    const removed = await deleteOne(
      requestTo(`/api/v1/items/${created.id}`, {
        method: "DELETE",
        user: alice,
      }),
      withId(created.id),
    );
    expect([removed.status, await removed.text()]).toEqual([204, ""]);
    await expectProblem(
      await getOne(
        requestTo(`/api/v1/items/${created.id}`, { user: alice }),
        withId(created.id),
      ),
      404,
      "not_found",
    );
  });

  it("sets Location on creation and never returns an owner id", async () => {
    const response = await POST(
      jsonRequest("/api/v1/items", "POST", { title: "Located" }, alice),
      undefined,
    );
    const text = await response.text();
    const item = itemResponseSchema.parse(JSON.parse(text));
    expect(response.headers.get("location")).toBe(`/api/v1/items/${item.id}`);
    expect(text).not.toMatch(new RegExp(`owner|${alice.id}`, "i"));
  });

  it("keeps users apart: each one lists, reads, changes and deletes only their own", async () => {
    const mine = await create(alice, "Alice only");
    const theirs = await create(bob, "Bob only");

    const aliceList = itemListResponseSchema.parse(
      await (
        await GET(
          requestTo("/api/v1/items?limit=100", { user: alice }),
          undefined,
        )
      ).json(),
    );
    expect(aliceList.items.map((item) => item.id)).toContain(mine.id);
    expect(aliceList.items.map((item) => item.id)).not.toContain(theirs.id);

    const missing = await getOne(
      requestTo(`/api/v1/items/${MISSING}`, { user: alice }),
      withId(MISSING),
    );
    const foreignRead = await getOne(
      requestTo(`/api/v1/items/${theirs.id}`, { user: alice }),
      withId(theirs.id),
    );
    const foreignPatch = await patchOne(
      jsonRequest(
        `/api/v1/items/${theirs.id}`,
        "PATCH",
        { title: "mine now" },
        alice,
      ),
      withId(theirs.id),
    );
    const foreignDelete = await deleteOne(
      requestTo(`/api/v1/items/${theirs.id}`, {
        method: "DELETE",
        user: alice,
      }),
      withId(theirs.id),
    );
    const reference = await expectProblem(missing, 404, "not_found");
    for (const response of [foreignRead, foreignPatch, foreignDelete]) {
      expect(await expectProblem(response, 404, "not_found")).toEqual(
        reference,
      ); // indistinguishable from a missing item
    }
    expect(
      (
        await admin("select title from app.items where id = $1", [theirs.id])
      )[0],
    ).toEqual({ title: "Bob only" });
  });

  it("answers a malformed or hostile id like a missing item, and never reaches the database with it", async () => {
    for (const id of [
      "1",
      "1 or 1=1",
      `${MISSING}' or '1'='1`,
      "'; drop table app.items; --",
      "%00",
      "../../etc/passwd",
    ]) {
      await expectProblem(
        await getOne(requestTo("/api/v1/items/x", { user: alice }), withId(id)),
        404,
        "not_found",
      );
      await expectProblem(
        await deleteOne(
          requestTo("/api/v1/items/x", { method: "DELETE", user: alice }),
          withId(id),
        ),
        404,
        "not_found",
      );
    }
    expect(
      await admin("select to_regclass('app.items') is not null as present"),
    ).toEqual([{ present: true }]);
  });

  it("validates every mutation and query through the shared Zod schemas", async () => {
    const bad = [
      jsonRequest("/api/v1/items", "POST", {}, alice),
      jsonRequest("/api/v1/items", "POST", { title: "" }, alice),
      jsonRequest(
        "/api/v1/items",
        "POST",
        { title: "ok", owner_id: bob.id },
        alice,
      ),
      jsonRequest("/api/v1/items", "POST", { title: "ok", id: MISSING }, alice),
      jsonRequest("/api/v1/items", "POST", { title: "x".repeat(101) }, alice),
    ];
    for (const request of bad) {
      const problem = await expectProblem(
        await POST(request, undefined),
        422,
        "validation_failed",
      );
      expect(JSON.stringify(problem)).not.toContain(bob.id);
    }
    await expectProblem(
      await patchOne(
        jsonRequest(`/api/v1/items/${MISSING}`, "PATCH", {}, alice),
        withId(MISSING),
      ),
      422,
      "validation_failed",
    );
    for (const query of [
      "limit=0",
      "limit=101",
      "limit=abc",
      "limit=1;drop",
      "offset=-1",
      "limit=",
      "other=1",
      "limit=1&limit=2&offset=x",
    ]) {
      await expectProblem(
        await GET(
          requestTo(`/api/v1/items?${query}`, { user: alice }),
          undefined,
        ),
        422,
        "validation_failed",
      );
    }
  });

  it("rejects malformed bodies, a wrong media type and an oversized body", async () => {
    const headers = (type: string) => ({ "content-type": type });
    await expectProblem(
      await POST(
        requestTo("/api/v1/items", {
          method: "POST",
          user: alice,
          headers: headers("application/json"),
          body: "{oops",
        }),
        undefined,
      ),
      400,
      "invalid_json",
    );
    await expectProblem(
      await POST(
        requestTo("/api/v1/items", {
          method: "POST",
          user: alice,
          headers: headers("text/plain"),
          body: "{}",
        }),
        undefined,
      ),
      415,
      "unsupported_media_type",
    );
    await expectProblem(
      await POST(
        requestTo("/api/v1/items", {
          method: "POST",
          user: alice,
          headers: headers("application/json"),
          body: JSON.stringify({ title: "x", body: "y".repeat(9_000) }),
        }),
        undefined,
      ),
      413,
      "payload_too_large",
    );
  });

  it("refuses state-changing requests from an origin that is not trusted, and allows the application's own", async () => {
    const attempt = (origin: string) =>
      POST(
        requestTo("/api/v1/items", {
          method: "POST",
          user: alice,
          headers: { "content-type": "application/json", origin },
          body: JSON.stringify({ title: `via ${origin}` }),
        }),
        undefined,
      );
    for (const origin of [
      "https://evil.example",
      "http://localhost:3001",
      "null",
    ]) {
      await expectProblem(await attempt(origin), 403, "origin_not_allowed");
    }
    expect((await attempt("http://localhost:3000")).status).toBe(201);
    expect(
      await admin(
        "select 1 from app.items where title like 'via %' and title <> 'via http://localhost:3000'",
      ),
    ).toEqual([]);
    const read = await GET(
      requestTo("/api/v1/items", {
        user: alice,
        headers: { origin: "https://evil.example" },
      }),
      undefined,
    );
    expect(read.status).toBe(200); // reads are not state-changing
  });

  it("reports a duplicate title as a field error about the caller's own data", async () => {
    await create(alice, "Unique Title");
    const response = await POST(
      jsonRequest("/api/v1/items", "POST", { title: "unique title" }, alice),
      undefined,
    );
    const problem = await expectProblem(response, 422, "validation_failed");
    expect(problem.errors).toEqual([
      { path: "title", message: "You already have an item with this title" },
    ]);
  });

  it("answers the other methods with 405 and the Allow header", async () => {
    for (const handler of [PUT, PATCH, DELETE]) {
      const response = handler();
      await expectProblem(response, 405, "method_not_allowed");
      expect(response.headers.get("allow")).toBe("GET, POST, OPTIONS");
    }
    expect(postOne().headers.get("allow")).toBe("GET, PATCH, DELETE, OPTIONS");
    expect(postSession().headers.get("allow")).toBe("GET, OPTIONS");
  });
});

describe("the caller's session", () => {
  it("describes the user and the session without a token", async () => {
    const response = await getSession(
      requestTo("/api/v1/session", { user: alice }),
    );
    const text = await response.text();
    const body = sessionResponseSchema.parse(JSON.parse(text));
    expect(body.user).toEqual({
      id: alice.id,
      email: alice.email,
      name: "alice",
    });
    expect(new Date(body.session.expiresAt).getTime()).toBeGreaterThan(
      Date.now(),
    );
    const token = (
      await admin<{ token: string }>(
        'select token from app_auth."session" where "userId" = $1',
        [alice.id],
      )
    )[0].token;
    expect(text).not.toContain(token);
    expect(text).not.toMatch(/password|token|hash/i);
  });

  it("lists only the caller's sessions, marks the current one and never shows a token", async () => {
    const response = await getSessions(
      requestTo("/api/v1/sessions", { user: bob }),
    );
    const text = await response.text();
    const body = sessionListResponseSchema.parse(JSON.parse(text));
    expect(body.sessions.length).toBeGreaterThanOrEqual(1);
    expect(body.sessions.filter((session) => session.current)).toHaveLength(1);
    const tokens = await admin<{ token: string }>(
      'select token from app_auth."session"',
    );
    for (const { token } of tokens) expect(text).not.toContain(token);
    const aliceIds = (
      await admin<{ id: string }>(
        'select id from app_auth."session" where "userId" = $1',
        [alice.id],
      )
    ).map((row) => row.id);
    for (const session of body.sessions)
      expect(aliceIds).not.toContain(session.id);
  });

  it("stops working when the session ends", async () => {
    const carol = await signUp("carol");
    expect(
      (await getSession(requestTo("/api/v1/session", { user: carol }))).status,
    ).toBe(200);
    await admin('delete from app_auth."session" where "userId" = $1', [
      carol.id,
    ]);
    await expectProblem(
      await getSession(requestTo("/api/v1/session", { user: carol })),
      401,
      "unauthenticated",
    );
    await expectProblem(
      await GET(requestTo("/api/v1/items", { user: carol }), undefined),
      401,
      "unauthenticated",
    );
  });
});

describe("when something goes wrong", () => {
  it("answers a database failure with the generic problem and leaks nothing", async () => {
    await forgetApplication();
    process.env.DATABASE_RUNTIME_URL =
      "postgresql://app_runtime:s3cr3t-route@127.0.0.1:1/app";
    process.env.DATABASE_CONNECT_TIMEOUT_MS = "1000";
    try {
      const response = await GET(
        requestTo("/api/v1/items", { user: alice }),
        undefined,
      );
      const text = await response.text();
      expect(response.status).toBe(500);
      expect(text).not.toMatch(
        /s3cr3t|127\.0\.0\.1|ECONNREFUSED|postgres|app_runtime/i,
      );
      await expectProblem(
        new Response(text, { status: 500, headers: response.headers }),
        500,
        "internal_error",
      );
    } finally {
      delete process.env.DATABASE_CONNECT_TIMEOUT_MS;
      await forgetApplication();
      process.env.DATABASE_RUNTIME_URL = database.runtimeUrl;
    }
  });
});
