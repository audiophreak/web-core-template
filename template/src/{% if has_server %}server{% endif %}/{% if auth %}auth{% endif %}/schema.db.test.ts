import { afterAll, beforeAll, describe, expect, it } from "vitest";
import pg from "pg";
import { getMigrations } from "better-auth/db/migration";
import { adminUrl } from "@/server/db/test-support/database";
import { getAuth } from "./auth";
import {
  createRuntimeDatabase,
  forgetApplication,
  type RuntimeDatabase,
} from "./test-support/harness";

/**
 * The application never changes its schema at runtime: Better Auth's tables come from the migrations in
 * supabase/migrations/. This asks Better Auth's own schema tooling what it would still have to create
 * or add after those migrations ran. The answer must be nothing; if a Better Auth upgrade (or a changed
 * option) needs more schema, this fails and a reviewed migration has to be added.
 */
let database: RuntimeDatabase;

async function admin(sql: string) {
  const url = new URL(adminUrl());
  url.pathname = `/${database.name}`;
  const client = new pg.Client({ connectionString: url.toString() });
  await client.connect();
  try {
    return (await client.query(sql)).rows;
  } finally {
    await client.end();
  }
}

beforeAll(async () => {
  database = await createRuntimeDatabase();
});

afterAll(async () => {
  await forgetApplication();
  await database.drop();
});

describe("Better Auth's schema", () => {
  it("is exactly what the committed migrations create", async () => {
    const { toBeCreated, toBeAdded } = await getMigrations(getAuth().options);
    expect(toBeCreated.map((table) => table.table)).toEqual([]);
    expect(toBeAdded.map((table) => table.table)).toEqual([]);
    const tables = await admin(
      "select table_name from information_schema.tables where table_schema = 'app_auth' order by 1",
    );
    expect(tables.map((row) => row.table_name)).toEqual([
      "account",
      "rateLimit",
      "session",
      "user",
      "verification",
    ]);
  });

  it("would notice a missing column (the check is not vacuous)", async () => {
    await admin('alter table app_auth."user" drop column "image"');
    const { toBeAdded } = await getMigrations(getAuth().options);
    expect(toBeAdded.map((table) => table.table)).toContain("user");
  });
});
