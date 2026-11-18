import { mkdtempSync, rmdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  MigrationError,
  addToLock,
  assertLocalHost,
  checksum,
  listMigrations,
  readLock,
  verifyLock,
} from "./migrations.mjs";

function directoryWith(files: Record<string, string>) {
  const directory = mkdtempSync(join(tmpdir(), "migrations-"));
  for (const [name, sql] of Object.entries(files))
    writeFileSync(join(directory, name), sql);
  return {
    directory,
    cleanup() {
      for (const name of Object.keys(files)) rmSync(join(directory, name));
      rmdirSync(directory);
    },
  };
}

describe("the committed migrations", () => {
  it("match the lock: a migration that was already committed has not been edited", () => {
    const migrations = listMigrations();
    expect(migrations.length).toBeGreaterThan(0);
    expect(verifyLock(migrations, readLock())).toEqual([]);
  });
});

describe("listMigrations", () => {
  it("returns migrations in version order with their names and checksums", () => {
    const { directory, cleanup } = directoryWith({
      "20260301000000_second.sql": "select 2;\n",
      "20260101000000_first.sql": "select 1;\n",
      ".gitkeep": "",
    });
    try {
      const listed = listMigrations(directory);
      expect(listed.map((m) => [m.version, m.name])).toEqual([
        ["20260101000000", "first"],
        ["20260301000000", "second"],
      ]);
      expect(listed[0].checksum).toBe(checksum("select 1;\n"));
    } finally {
      cleanup();
    }
  });

  it.each([
    [
      "a name without a version",
      { "create_items.sql": "x" },
      /migration files are named/,
    ],
    ["a short version", { "2026_items.sql": "x" }, /migration files are named/],
    [
      "upper-case letters",
      { "20260101000000_Items.sql": "x" },
      /migration files are named/,
    ],
    [
      "a duplicate version",
      { "20260101000000_a.sql": "x", "20260101000000_b.sql": "y" },
      /used by more than one migration/,
    ],
  ])("rejects %s", (_label, files, message) => {
    const { directory, cleanup } = directoryWith(files);
    try {
      expect(() => listMigrations(directory)).toThrow(message);
    } finally {
      cleanup();
    }
  });

  it("rejects a missing directory", () => {
    expect(() =>
      listMigrations(join(tmpdir(), "no-such-migrations-directory")),
    ).toThrow(MigrationError);
  });
});

describe("checksum", () => {
  it("does not depend on the line-ending style of the checkout", () => {
    expect(checksum("a\r\nb\r\n")).toBe(checksum("a\nb\n"));
    expect(checksum("a\nb\n")).not.toBe(checksum("a\nc\n"));
  });
});

describe("the migrations lock", () => {
  const migration = (version: string, name: string, sql: string) => ({
    version,
    name,
    file: `${version}_${name}.sql`,
    sql,
    checksum: checksum(sql),
  });
  const first = migration("20260101000000", "first", "select 1;");

  it("records new migrations and never rewrites an existing entry", () => {
    const { lock, added } = addToLock([first], { version: 1, migrations: {} });
    expect(added).toEqual([first.file]);
    expect(verifyLock([first], lock)).toEqual([]);
    expect(() =>
      addToLock([{ ...first, checksum: checksum("select 'edited';") }], lock),
    ).toThrow(/modified after it was locked/);
  });

  it("reports modified, unlocked, renamed and missing migrations", () => {
    const { lock } = addToLock([first], { version: 1, migrations: {} });
    expect(
      verifyLock([{ ...first, checksum: checksum("other") }], lock)[0],
    ).toMatch(/modified after it was locked/);
    expect(
      verifyLock(
        [first, migration("20260201000000", "second", "select 2;")],
        lock,
      )[0],
    ).toMatch(/not in the lock/);
    expect(verifyLock([{ ...first, name: "renamed" }], lock)[0]).toMatch(
      /renamed/,
    );
    expect(verifyLock([], lock)[0]).toMatch(/file is missing/);
  });
});

describe("assertLocalHost", () => {
  it.each([
    "postgresql://u:p@localhost:5432/x",
    "postgresql://u:p@127.0.0.1:5432/x",
    "postgresql://u:p@[::1]:5432/x",
    "postgresql://u@host.docker.internal/x",
  ])("accepts the local database %s", (url) => {
    expect(() => assertLocalHost(url)).not.toThrow();
  });

  it("refuses any other host unless explicitly allowed, without printing the URL", () => {
    expect(() =>
      assertLocalHost("postgresql://u:s3cr3t@db.example.com:5432/x"),
    ).toThrow(/non-local database \(db\.example\.com\)/);
    try {
      assertLocalHost("postgresql://u:s3cr3t@db.example.com:5432/x");
    } catch (error) {
      expect((error as Error).message).not.toContain("s3cr3t");
    }
    expect(() =>
      assertLocalHost("postgresql://u:p@db.example.com/x", true),
    ).not.toThrow();
    expect(() => assertLocalHost("not a url")).toThrow(MigrationError);
  });
});
