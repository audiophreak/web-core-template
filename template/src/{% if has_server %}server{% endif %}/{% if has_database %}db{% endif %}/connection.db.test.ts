import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import {
  DatabaseConfigError,
  DatabaseError,
  closePool,
  getPool,
  withTransaction,
} from "@/server/db";
import { listItems } from "@/server/items/repository";
import {
  adminUrl,
  createTestDatabase,
  type TestDatabase,
} from "@/server/db/test-support/database";

let database: TestDatabase;

beforeAll(async () => {
  database = await createTestDatabase();
});

afterEach(async () => {
  await closePool();
});

afterAll(async () => {
  await database.drop();
});

async function code(promise: Promise<unknown>): Promise<string> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof DatabaseError) {
      expect(error.message).not.toMatch(
        /s3cr3t|postgres:|127\.0\.0\.1|password|app_test_/i,
      );
      return error.code;
    }
    throw error;
  }
  return "no error";
}

describe("connection failures", () => {
  it("reports an unreachable server as unavailable, quickly, without leaking the address", async () => {
    process.env.DATABASE_URL = "postgresql://app:s3cr3t@127.0.0.1:1/app";
    process.env.DATABASE_CONNECT_TIMEOUT_MS = "2000";
    const started = Date.now();
    expect(await code(listItems())).toBe("unavailable");
    expect(await code(withTransaction(async () => undefined))).toBe(
      "unavailable",
    );
    expect(Date.now() - started).toBeLessThan(5_000);
  });

  it("reports rejected credentials as unavailable, not as a detail about the password", async () => {
    const url = new URL(adminUrl());
    url.password = "s3cr3t-wrong";
    url.pathname = `/${database.name}`;
    process.env.DATABASE_URL = url.toString();
    expect(await code(listItems())).toBe("unavailable");
  });

  it("reports a missing database as unavailable", async () => {
    const url = new URL(database.url);
    url.pathname = "/app_test_does_not_exist";
    process.env.DATABASE_URL = url.toString();
    expect(await code(listItems())).toBe("unavailable");
  });

  it("times out a connection attempt that never completes instead of hanging", async () => {
    process.env.DATABASE_URL = "postgresql://app:s3cr3t@10.255.255.1:5432/app"; // a non-routable address
    process.env.DATABASE_CONNECT_TIMEOUT_MS = "500";
    const started = Date.now();
    expect(["timeout", "unavailable"]).toContain(await code(listItems()));
    expect(Date.now() - started).toBeLessThan(4_000);
  });

  it("recovers once the database is reachable again", async () => {
    process.env.DATABASE_URL = "postgresql://app:s3cr3t@127.0.0.1:1/app";
    expect(await code(listItems())).toBe("unavailable");
    await closePool();
    process.env.DATABASE_URL = database.url;
    process.env.DATABASE_CONNECT_TIMEOUT_MS = "5000";
    expect(await listItems()).toEqual([]);
  });
});

describe("configuration errors", () => {
  it("fails with the names of the bad variables, never their values", () => {
    process.env.DATABASE_URL = "mysql://app:s3cr3t@127.0.0.1:3306/app";
    try {
      getPool();
      throw new Error("expected a configuration error");
    } catch (error) {
      expect(error).toBeInstanceOf(DatabaseConfigError);
      expect((error as Error).message).not.toContain("s3cr3t");
    }
  });
});
