import { randomBytes } from "node:crypto";
import { existsSync } from "node:fs";
import pg from "pg";
import {
  assertLocalHost,
  migrate,
} from "../../../../scripts/db/migrations.mjs";

/**
 * Disposable databases for the real-database tests. DATABASE_URL (from the environment or .env.local)
 * names a LOCAL server; each test file creates its own database on it, `app_test_<random>`, and drops
 * only that one. The database named in the URL is never modified.
 */
let admin: string | undefined;

/** The administrator connection, read once: tests change DATABASE_URL, and cleanup must still reach this server. */
export function adminUrl(): string {
  if (admin) return admin;
  if (!process.env.DATABASE_URL && existsSync(".env.local"))
    process.loadEnvFile(".env.local");
  const url = process.env.DATABASE_URL;
  if (!url)
    throw new Error(
      "DATABASE_URL is not set: run npm run db:init and npm run db:up, or set it in the environment",
    );
  assertLocalHost(url, process.env.MIGRATE_ALLOW_REMOTE === "true");
  admin = url;
  return admin;
}

export type TestDatabase = {
  name: string;
  url: string;
  drop: () => Promise<void>;
};

function urlFor(name: string): string {
  const url = new URL(adminUrl());
  url.pathname = `/${name}`;
  return url.toString();
}

export async function createTestDatabase({
  migrated = true,
}: { migrated?: boolean } = {}): Promise<TestDatabase> {
  const name = `app_test_${randomBytes(6).toString("hex")}`; // hexadecimal only: safe to place in SQL
  const admin = new pg.Client({ connectionString: adminUrl() });
  await admin.connect();
  try {
    await admin.query(`create database ${name}`);
  } finally {
    await admin.end();
  }
  const url = urlFor(name);
  if (migrated) await migrate({ connectionString: url });
  return {
    name,
    url,
    async drop() {
      const client = new pg.Client({ connectionString: adminUrl() });
      await client.connect();
      try {
        await client.query(`drop database if exists ${name} with (force)`);
      } finally {
        await client.end();
      }
    },
  };
}
