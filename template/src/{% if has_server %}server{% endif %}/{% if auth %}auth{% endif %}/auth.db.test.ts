import { afterAll, beforeAll, describe, expect, it } from "vitest";
import pg from "pg";
import { adminUrl } from "@/server/db/test-support/database";
import { getPool } from "@/server/db/pool";
import { createAuth, getAuth } from "./auth";
import { loadAuthConfig } from "./config";
import { principalFromHeaders, sessionFromHeaders } from "./principal";
import {
  ORIGIN,
  PASSWORD,
  cookieOf,
  createRuntimeDatabase,
  forgetApplication,
  requestTo,
  signUp,
  type RuntimeDatabase,
} from "./test-support/harness";

/**
 * Better Auth against a real PostgreSQL, through the application's own configuration: sign-up, sign-in,
 * sessions, sign-out, revocation, expiry, cookies, origin checks and rate limits.
 */
let database: RuntimeDatabase;

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

const post = (
  path: string,
  body: unknown,
  headers: Record<string, string> = {},
) =>
  requestTo(path, {
    method: "POST",
    headers: { "content-type": "application/json", origin: ORIGIN, ...headers },
    body: JSON.stringify(body),
  });

/** The application's configuration without the rate limiter, so tests are not throttled by each other. */
const open = () =>
  createAuth(getPool(), loadAuthConfig(), { rateLimit: false });

beforeAll(async () => {
  database = await createRuntimeDatabase();
});

afterAll(async () => {
  await forgetApplication();
  await database.drop();
});

describe("registration and credentials", () => {
  it("creates a user and a session, and reads the session back from its cookie", async () => {
    const user = await signUp("first");
    const current = await sessionFromHeaders(user.headers);
    expect(current?.user).toEqual({
      id: user.id,
      email: user.email,
      name: "first",
    });
    expect(current?.session.expiresAt.getTime()).toBeGreaterThan(Date.now());
  });

  it("refuses a second account with the same email", async () => {
    const user = await signUp("dupe");
    const response = await open().handler(
      post("/api/auth/sign-up/email", {
        email: user.email,
        password: PASSWORD,
        name: "again",
      }),
    );
    expect(response.status).toBeGreaterThanOrEqual(400);
    const rows = await admin(
      'select id from app_auth."user" where email = $1',
      [user.email],
    );
    expect(rows).toHaveLength(1);
  });

  it("refuses a password shorter than the minimum", async () => {
    const response = await open().handler(
      post("/api/auth/sign-up/email", {
        email: "short@example.test",
        password: "short",
        name: "short",
      }),
    );
    expect(response.status).toBe(400);
  });

  it("signs in with the right password and answers the same for a wrong password and an unknown email", async () => {
    const user = await signUp("login");
    const ok = await open().handler(
      post("/api/auth/sign-in/email", {
        email: user.email,
        password: PASSWORD,
      }),
    );
    expect(ok.status).toBe(200);
    expect(cookieOf(ok)).toContain("session_token=");

    const wrong = await open().handler(
      post(
        "/api/auth/sign-in/email",
        { email: user.email, password: "wrong password entirely" },
        { "x-forwarded-for": "198.51.100.1" },
      ),
    );
    const unknown = await open().handler(
      post(
        "/api/auth/sign-in/email",
        { email: "nobody@example.test", password: "wrong password entirely" },
        { "x-forwarded-for": "198.51.100.2" },
      ),
    );
    expect([wrong.status, unknown.status]).toEqual([401, 401]);
    expect(await wrong.text()).toBe(await unknown.text()); // nothing tells the two apart
  });

  it("stores a salted hash, never the password", async () => {
    const user = await signUp("hashed", "a very unusual passphrase 7261");
    const [account] = await admin<{ password: string }>(
      'select password from app_auth."account" where "userId" = $1',
      [user.id],
    );
    expect(account.password).not.toContain("unusual");
    expect(account.password).toMatch(/^[0-9a-f]+:[0-9a-f]+$/); // salt:hash
    for (const table of ["user", "session", "account", "verification"]) {
      const dump = await admin<{ text: string }>(
        `select coalesce(string_agg(t::text, ' '), '') as text from app_auth."${table}" t`,
      );
      expect(dump[0].text).not.toContain("unusual passphrase");
    }
  });
});

describe("sessions", () => {
  it("ends a session at sign-out: the cookie stops working and the row is gone", async () => {
    const user = await signUp("out");
    expect(await principalFromHeaders(user.headers)).not.toBeNull();
    const response = await open().handler(
      requestTo("/api/auth/sign-out", {
        method: "POST",
        user,
        headers: { origin: ORIGIN, "content-type": "application/json" },
        body: "{}",
      }),
    );
    expect(response.status).toBe(200);
    expect(await principalFromHeaders(user.headers)).toBeNull();
    expect(
      await admin('select 1 from app_auth."session" where "userId" = $1', [
        user.id,
      ]),
    ).toHaveLength(0);
  });

  it("rejects a missing, forged, tampered or unknown session cookie", async () => {
    const user = await signUp("forge");
    const value = user.cookie.split("=").slice(1).join("=");
    const name = user.cookie.split("=")[0];
    const cases = [
      new Headers(),
      new Headers({ cookie: `${name}=${"a".repeat(value.length)}` }),
      new Headers({ cookie: `${name}=${value.slice(0, -4)}AAAA` }),
      new Headers({ cookie: `${name}=${value.split(".")[0]}` }), // token without its signature
      new Headers({ cookie: `${name}=garbage.garbage` }),
    ];
    for (const headers of cases)
      expect(await principalFromHeaders(headers)).toBeNull();
    expect(await principalFromHeaders(user.headers)).not.toBeNull();
  });

  it("rejects an expired session", async () => {
    const user = await signUp("expired");
    await admin(
      `update app_auth."session" set "expiresAt" = now() - interval '1 minute' where "userId" = $1`,
      [user.id],
    );
    expect(await principalFromHeaders(user.headers)).toBeNull();
  });

  it("rejects a revoked session and keeps the user's other session", async () => {
    const user = await signUp("revoke");
    const second = await open().handler(
      post("/api/auth/sign-in/email", {
        email: user.email,
        password: user.password,
      }),
    );
    const secondCookie = new Headers({ cookie: cookieOf(second) });
    const revoked = await admin<{ id: string }>(
      'delete from app_auth."session" where "userId" = $1 and token = (select token from app_auth."session" where "userId" = $1 order by "createdAt" limit 1) returning id',
      [user.id],
    );
    expect(revoked).toHaveLength(1);
    expect(await principalFromHeaders(user.headers)).toBeNull();
    expect(await principalFromHeaders(secondCookie)).not.toBeNull();
  });

  it("revokes the other sessions when the password changes, and the old password stops working", async () => {
    const user = await signUp("change");
    const other = await open().handler(
      post("/api/auth/sign-in/email", {
        email: user.email,
        password: user.password,
      }),
    );
    const otherHeaders = new Headers({ cookie: cookieOf(other) });
    expect(await principalFromHeaders(otherHeaders)).not.toBeNull();

    const changed = await open().handler(
      requestTo("/api/auth/change-password", {
        method: "POST",
        user,
        headers: { origin: ORIGIN, "content-type": "application/json" },
        body: JSON.stringify({
          currentPassword: user.password,
          newPassword: "a brand new passphrase 99",
          revokeOtherSessions: true,
        }),
      }),
    );
    expect(changed.status).toBe(200);
    expect(await principalFromHeaders(otherHeaders)).toBeNull();
    const oldLogin = await open().handler(
      post(
        "/api/auth/sign-in/email",
        { email: user.email, password: user.password },
        { "x-forwarded-for": "198.51.100.9" },
      ),
    );
    expect(oldLogin.status).toBe(401);
  });

  it("survives an application restart: a new instance on a new pool accepts the same cookie", async () => {
    const user = await signUp("restart");
    await forgetApplication();
    process.env.DATABASE_RUNTIME_URL = database.runtimeUrl;
    expect(await principalFromHeaders(user.headers)).not.toBeNull();
  });
});

describe("cookies, origins and rate limits", () => {
  it("issues an HttpOnly, SameSite=Lax cookie that is not Secure on plain http", async () => {
    const user = await signUp("cookie");
    const response = await open().handler(
      post("/api/auth/sign-in/email", {
        email: user.email,
        password: user.password,
      }),
    );
    const header = response.headers
      .getSetCookie()
      .find((line) => line.includes("session_token"))!;
    expect(header).toMatch(/^better-auth\.session_token=/);
    expect(header).toMatch(/;\s*HttpOnly/i);
    expect(header).toMatch(/;\s*SameSite=Lax/i);
    expect(header).toMatch(/;\s*Path=\//i);
    expect(header).toMatch(/;\s*Max-Age=604800/i);
    expect(header).not.toMatch(/;\s*Secure/i);
  });

  it("marks the cookie Secure, with the __Secure- prefix, when the base URL is https", async () => {
    const user = await signUp("secure");
    const secure = createAuth(
      getPool(),
      loadAuthConfig({
        BETTER_AUTH_SECRET: process.env.BETTER_AUTH_SECRET,
        BETTER_AUTH_URL: "https://app.example.test",
        NODE_ENV: "production",
      }),
      { rateLimit: false },
    );
    const response = await secure.handler(
      new Request("https://app.example.test/api/auth/sign-in/email", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          origin: "https://app.example.test",
        },
        body: JSON.stringify({ email: user.email, password: user.password }),
      }),
    );
    expect(response.status).toBe(200);
    const header = response.headers
      .getSetCookie()
      .find((line) => line.includes("session_token"))!;
    expect(header).toMatch(/^__Secure-better-auth\.session_token=/);
    expect(header).toMatch(/;\s*Secure/i);
    expect(header).toMatch(/;\s*HttpOnly/i);
    expect(header).toMatch(/;\s*SameSite=Lax/i);
  });

  it("refuses a state-changing request from an origin that is not trusted", async () => {
    const user = await signUp("origin");
    for (const origin of [
      "https://evil.example",
      "http://localhost:3001",
      "null",
    ]) {
      const response = await open().handler(
        requestTo("/api/auth/sign-out", {
          method: "POST",
          user,
          headers: { origin, "content-type": "application/json" },
          body: "{}",
        }),
      );
      expect(response.status, origin).toBe(403);
    }
    expect(await principalFromHeaders(user.headers)).not.toBeNull(); // still signed in
  });

  it("cannot send a password reset: no mail service exists, so the endpoint is switched off", async () => {
    const user = await signUp("reset");
    const response = await open().handler(
      post("/api/auth/request-password-reset", {
        email: user.email,
        redirectTo: ORIGIN,
      }),
    );
    expect(response.status).toBe(400);
    expect(((await response.json()) as { code?: string }).code).toBe(
      "RESET_PASSWORD_DISABLED",
    );
  });

  it("disables open sign-up in production unless it is enabled on purpose", async () => {
    const production = loadAuthConfig({
      BETTER_AUTH_SECRET: process.env.BETTER_AUTH_SECRET,
      BETTER_AUTH_URL: "https://app.example.test",
      NODE_ENV: "production",
    });
    expect(production.signUpEnabled).toBe(false);
    const closed = createAuth(getPool(), production, { rateLimit: false });
    const response = await closed.handler(
      new Request("https://app.example.test/api/auth/sign-up/email", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          origin: "https://app.example.test",
        },
        body: JSON.stringify({
          email: "closed@example.test",
          password: PASSWORD,
          name: "closed",
        }),
      }),
    );
    expect(response.status).toBeGreaterThanOrEqual(400);
    expect(
      await admin('select 1 from app_auth."user" where email = $1', [
        "closed@example.test",
      ]),
    ).toHaveLength(0);
  });

  it("limits repeated sign-in attempts, and a spoofed X-Forwarded-For does not give a fresh allowance", async () => {
    const statuses: number[] = [];
    for (let attempt = 0; attempt < 8; attempt += 1) {
      const response = await getAuth().handler(
        post(
          "/api/auth/sign-in/email",
          { email: "limit@example.test", password: "wrong password entirely" },
          { "x-forwarded-for": `203.0.113.${attempt + 1}` },
        ),
      );
      statuses.push(response.status);
    }
    expect(statuses).toContain(429);
    expect(
      statuses.filter((status) => status === 401).length,
    ).toBeLessThanOrEqual(3);
  });
});
