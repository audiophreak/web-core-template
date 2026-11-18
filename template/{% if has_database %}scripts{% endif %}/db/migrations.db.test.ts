import { mkdtempSync, rmdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import pg from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  createTestDatabase,
  type TestDatabase,
} from "@/server/db/test-support/database";
import { MigrationError, migrate, migrationStatus } from "./migrations.mjs";

const FIRST = "create table first_table (id int primary key);\n";
let database: TestDatabase;
let directory: string;
const files = new Set<string>();

function write(name: string, sql: string) {
  writeFileSync(join(directory, name), sql);
  files.add(name);
}

async function query(sql: string) {
  const client = new pg.Client({ connectionString: database.url });
  await client.connect();
  try {
    return (await client.query(sql)).rows;
  } finally {
    await client.end();
  }
}

beforeAll(async () => {
  database = await createTestDatabase({ migrated: false });
  directory = mkdtempSync(join(tmpdir(), "migrations-db-"));
});

afterAll(async () => {
  for (const name of files) rmSync(join(directory, name), { force: true });
  rmdirSync(directory);
  await database.drop();
});

describe("migration runner against a real database", () => {
  it("creates the schema on a fresh database and records version, name and checksum", async () => {
    write("20260101000000_first.sql", FIRST);

    const result = await migrate({ connectionString: database.url, directory });

    expect(result.applied).toEqual(["20260101000000_first.sql"]);
    expect(
      await query(
        "select to_regclass('public.first_table') is not null as present",
      ),
    ).toEqual([{ present: true }]);
    expect(
      await query(
        "select version, name, length(checksum) as n from public.schema_migrations",
      ),
    ).toEqual([{ version: "20260101000000", name: "first", n: 64 }]);
  });

  it("does nothing the second time", async () => {
    const before = await query(
      "select applied_at from public.schema_migrations",
    );
    const result = await migrate({ connectionString: database.url, directory });
    expect(result.applied).toEqual([]);
    expect(
      await query("select applied_at from public.schema_migrations"),
    ).toEqual(before);
  });

  it("applies a later migration and keeps existing data", async () => {
    await query("insert into first_table values (1)");
    write(
      "20260201000000_second.sql",
      "alter table first_table add column note text default 'kept';\n",
    );

    await migrate({ connectionString: database.url, directory });

    expect(await query("select id, note from first_table")).toEqual([
      { id: 1, note: "kept" },
    ]);
  });

  it("refuses an applied migration that was edited, and one whose file disappeared", async () => {
    write("20260101000000_first.sql", `${FIRST}-- edited\n`);
    await expect(
      migrate({ connectionString: database.url, directory }),
    ).rejects.toThrow(/was modified after it was applied/);
    write("20260101000000_first.sql", FIRST);
    rmSync(join(directory, "20260201000000_second.sql"));
    files.delete("20260201000000_second.sql");
    await expect(
      migrate({ connectionString: database.url, directory }),
    ).rejects.toThrow(/has no file any more/);
    write(
      "20260201000000_second.sql",
      "alter table first_table add column note text default 'kept';\n",
    );
  });

  it("rolls a failing migration back completely and records nothing", async () => {
    write(
      "20260301000000_broken.sql",
      "create table half_done (id int);\nselect 1/0;\n",
    );

    await expect(
      migrate({ connectionString: database.url, directory }),
    ).rejects.toThrow(/20260301000000_broken\.sql failed and was rolled back/);

    expect(
      await query("select to_regclass('public.half_done') is null as absent"),
    ).toEqual([{ absent: true }]);
    expect(
      await query("select count(*)::int as n from public.schema_migrations"),
    ).toEqual([{ n: 2 }]);
    rmSync(join(directory, "20260301000000_broken.sql"));
    files.delete("20260301000000_broken.sql");
  });

  it("refuses a new migration that is older than one already applied", async () => {
    write("20260115000000_late.sql", "create table late_table (id int);\n");

    await expect(
      migrate({ connectionString: database.url, directory }),
    ).rejects.toThrow(/older than the newest applied migration/);
    expect(
      await query("select to_regclass('public.late_table') is null as absent"),
    ).toEqual([{ absent: true }]);
    rmSync(join(directory, "20260115000000_late.sql"));
    files.delete("20260115000000_late.sql");
  });

  it("reports what is applied and what is pending without changing anything", async () => {
    write("20260401000000_pending.sql", "select 1;\n");

    const status = await migrationStatus({
      connectionString: database.url,
      directory,
    });

    expect(status.applied).toEqual([
      "20260101000000_first",
      "20260201000000_second",
    ]);
    expect(status.pending).toEqual(["20260401000000_pending.sql"]);
    expect(status.problems).toEqual([]);
    expect(
      await query("select count(*)::int as n from public.schema_migrations"),
    ).toEqual([{ n: 2 }]);
  });

  it("applies migrations in version order, however the files were created", async () => {
    write("20260402000000_b.sql", "create table order_b (id int);\n");
    write("20260403000000_c.sql", "create table order_c (id int);\n");
    await migrate({ connectionString: database.url, directory });
    expect(
      (
        await query(
          "select version from public.schema_migrations order by applied_at, version",
        )
      ).map((row) => row.version),
    ).toEqual([
      "20260101000000",
      "20260201000000",
      "20260401000000",
      "20260402000000",
      "20260403000000",
    ]);
  });

  it("fails visibly when the database cannot be reached", async () => {
    await expect(
      migrate({
        connectionString: "postgresql://u:s3cr3t@127.0.0.1:1/x",
        directory,
      }),
    ).rejects.toThrow(MigrationError);
    await expect(
      migrate({
        connectionString: "postgresql://u:s3cr3t@127.0.0.1:1/x",
        directory,
      }),
    ).rejects.not.toThrow(/s3cr3t/);
  });
});
