import { randomBytes } from "node:crypto";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { expectProblem } from "@/server/api/expect-problem";
import { resetAuth } from "./auth";
import { withAuthentication } from "./guard";

/**
 * An untrusted origin on a state-changing request is refused with a 403 before the session is even looked
 * at, and the handler never runs. That needs no database. Everything that involves a session (the 401 for
 * a missing or forged cookie, real users) is tested against a real database in auth.db.test.ts and in the
 * route tests.
 */
const saved = { ...process.env };

beforeAll(() => {
  process.env.BETTER_AUTH_SECRET = randomBytes(32).toString("hex");
  process.env.BETTER_AUTH_URL = "http://localhost:3000";
  process.env.DATABASE_RUNTIME_URL =
    "postgresql://app_runtime:unused-password-for-this-test@127.0.0.1:1/app";
  resetAuth();
});

afterAll(() => {
  process.env = saved;
  resetAuth();
});

let reached = 0;
const guarded = withAuthentication(async () => {
  reached += 1;
  return new Response("handler ran");
});
const request = (method: string, headers: Record<string, string> = {}) =>
  new Request("http://localhost:3000/api/v1/anything", { method, headers });

describe("withAuthentication", () => {
  it("refuses a state-changing request from an origin that is not trusted, before looking at the session", async () => {
    for (const method of ["POST", "PATCH", "PUT", "DELETE"])
      await expectProblem(
        await guarded(
          request(method, { origin: "https://evil.example" }),
          undefined,
        ),
        403,
        "origin_not_allowed",
      );
    expect(reached).toBe(0);
  });

  it("turns a configuration error into the generic problem without leaking the setting", async () => {
    const secret = process.env.BETTER_AUTH_SECRET;
    process.env.BETTER_AUTH_SECRET = "short";
    resetAuth();
    try {
      const response = await guarded(request("GET"), undefined);
      const text = await response.text();
      expect(response.status).toBe(500);
      expect(text).not.toMatch(/BETTER_AUTH|short|secret/i);
    } finally {
      process.env.BETTER_AUTH_SECRET = secret;
      resetAuth();
    }
  });
});
