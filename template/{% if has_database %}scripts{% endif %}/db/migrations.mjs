// Plain SQL migrations: one canonical source (supabase/migrations) for both providers.
//
// - Files are `<14-digit version>_<name>.sql` and run in version order, each in its own transaction.
// - supabase/migrations.lock.json records every migration's SHA-256 so that editing a migration that
//   was already committed is caught offline (`npm run db:verify`, and the unit tests in `npm run check`).
// - With PostgreSQL, applied versions and checksums are recorded in public.schema_migrations; an applied
//   migration that changed or disappeared, or a new migration older than an applied one, stops the run.
//   With Supabase the CLI keeps its own history (supabase_migrations.schema_migrations) and applies the
//   same files; only the lock check applies to both.
import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import pg from "pg";

export const MIGRATIONS_DIR = "supabase/migrations";
export const LOCK_FILE = "supabase/migrations.lock.json";
const FILE_NAME = /^(\d{14})_([a-z][a-z0-9_]*)\.sql$/;
const ADVISORY_LOCK = 7_240_126; // serializes concurrent runs against one database

export class MigrationError extends Error {}

/** SHA-256 of the migration text with line endings normalized, so a checkout setting cannot change it. */
export function checksum(sql) {
  return createHash("sha256").update(sql.replace(/\r\n/g, "\n")).digest("hex");
}

/** The migrations in `directory`, in version order. Any `.sql` file that is not named correctly is an error. */
export function listMigrations(directory = MIGRATIONS_DIR) {
  if (!existsSync(directory)) throw new MigrationError(`${directory} does not exist`);
  const found = [];
  for (const file of readdirSync(directory).filter((name) => name.endsWith(".sql")).sort()) {
    const match = FILE_NAME.exec(file);
    if (!match) throw new MigrationError(`${file}: migration files are named <14-digit version>_<name>.sql (lowercase, underscores)`);
    const sql = readFileSync(join(directory, file), "utf8");
    found.push({ version: match[1], name: match[2], file, sql, checksum: checksum(sql) });
  }
  const versions = found.map((m) => m.version);
  const duplicate = versions.find((v, i) => versions.indexOf(v) !== i);
  if (duplicate) throw new MigrationError(`version ${duplicate} is used by more than one migration`);
  return found;
}

export function readLock(path = LOCK_FILE) {
  if (!existsSync(path)) return { version: 1, migrations: {} };
  const lock = JSON.parse(readFileSync(path, "utf8"));
  if (lock.version !== 1 || typeof lock.migrations !== "object" || lock.migrations === null) {
    throw new MigrationError(`${path} is not a version 1 migrations lock`);
  }
  return lock;
}

/** Problems between the migration files and the lock (empty when they agree). */
export function verifyLock(migrations, lock) {
  const problems = [];
  for (const m of migrations) {
    const entry = lock.migrations[m.version];
    if (!entry) problems.push(`${m.file} is not in the lock: run npm run db:lock to record it`);
    else if (entry.sha256 !== m.checksum) problems.push(`${m.file} was modified after it was locked: never edit a committed migration, add a new one`);
    else if (entry.name !== m.name) problems.push(`${m.file} was renamed (locked as ${entry.name})`);
  }
  for (const version of Object.keys(lock.migrations)) {
    if (!migrations.some((m) => m.version === version)) problems.push(`migration ${version}_${lock.migrations[version].name} is in the lock but its file is missing`);
  }
  return problems;
}

/** Adds migrations that are not locked yet. Existing entries are never rewritten; a changed one is an error. */
export function addToLock(migrations, lock) {
  const problems = verifyLock(migrations, lock).filter((p) => !p.includes("is not in the lock"));
  if (problems.length) throw new MigrationError(problems.join("\n"));
  const next = { version: 1, migrations: { ...lock.migrations } };
  const added = [];
  for (const m of migrations) {
    if (!next.migrations[m.version]) {
      next.migrations[m.version] = { name: m.name, sha256: m.checksum };
      added.push(m.file);
    }
  }
  return { lock: next, added };
}

export function writeLock(lock, path = LOCK_FILE) {
  const ordered = Object.fromEntries(Object.entries(lock.migrations).sort(([a], [b]) => a.localeCompare(b)));
  writeFileSync(path, `${JSON.stringify({ version: 1, migrations: ordered }, null, 2)}\n`);
}

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]", "host.docker.internal"]);

/** Migrations run from a developer machine only against a local database unless explicitly allowed. */
export function assertLocalHost(connectionString, allowRemote = false) {
  let host;
  try {
    host = new URL(connectionString).hostname;
  } catch {
    throw new MigrationError("DATABASE_URL is not a valid URL");
  }
  if (!allowRemote && !LOCAL_HOSTS.has(host)) {
    throw new MigrationError(`refusing to migrate a non-local database (${host}); set MIGRATE_ALLOW_REMOTE=true if that is intended`);
  }
}

async function withClient(connectionString, work) {
  const client = new pg.Client({ connectionString, connectionTimeoutMillis: 5000, statement_timeout: 120_000, application_name: "migrations" });
  try {
    await client.connect();
  } catch (error) {
    throw new MigrationError(`cannot connect to the database (${error.code ?? error.message})`);
  }
  try {
    await client.query("select pg_advisory_lock($1)", [ADVISORY_LOCK]);
    return await work(client);
  } finally {
    await client.end().catch(() => {});
  }
}

const BOOKKEEPING = `create table if not exists public.schema_migrations (
  version text primary key,
  name text not null,
  checksum text not null,
  applied_at timestamptz not null default now()
)`;

function plan(migrations, applied) {
  const problems = [];
  const byVersion = new Map(migrations.map((m) => [m.version, m]));
  for (const row of applied) {
    const file = byVersion.get(row.version);
    if (!file) problems.push(`applied migration ${row.version}_${row.name} has no file any more`);
    else if (file.checksum !== row.checksum) problems.push(`${file.file} was modified after it was applied (checksum differs): never edit an applied migration, add a new one`);
  }
  const done = new Set(applied.map((r) => r.version));
  const newest = applied.map((r) => r.version).sort().at(-1);
  const pending = migrations.filter((m) => !done.has(m.version));
  for (const m of pending) {
    if (newest && m.version < newest) problems.push(`${m.file} is older than the newest applied migration (${newest}): new migrations need a later version`);
  }
  return { pending, problems };
}

/** What would run: applied and pending migrations, plus any inconsistency. Changes nothing. */
export async function migrationStatus({ connectionString, directory = MIGRATIONS_DIR }) {
  const migrations = listMigrations(directory);
  return withClient(connectionString, async (client) => {
    const exists = await client.query("select to_regclass('public.schema_migrations') is not null as present");
    const applied = exists.rows[0].present ? (await client.query("select version, name, checksum from public.schema_migrations order by version")).rows : [];
    const { pending, problems } = plan(migrations, applied);
    return { applied: applied.map((r) => `${r.version}_${r.name}`), pending: pending.map((m) => m.file), problems };
  });
}

/** Applies pending migrations in order, each in its own transaction. Stops at the first problem. */
export async function migrate({ connectionString, directory = MIGRATIONS_DIR, log = () => {} }) {
  const migrations = listMigrations(directory);
  return withClient(connectionString, async (client) => {
    await client.query(BOOKKEEPING);
    const applied = (await client.query("select version, name, checksum from public.schema_migrations order by version")).rows;
    const { pending, problems } = plan(migrations, applied);
    if (problems.length) throw new MigrationError(problems.join("\n"));
    const ran = [];
    for (const m of pending) {
      try {
        await client.query("begin");
        await client.query(m.sql);
        await client.query("insert into public.schema_migrations (version, name, checksum) values ($1, $2, $3)", [m.version, m.name, m.checksum]);
        await client.query("commit");
      } catch (error) {
        await client.query("rollback").catch(() => {});
        throw new MigrationError(`${m.file} failed and was rolled back: ${error.message}`);
      }
      log(`applied ${m.file}`);
      ran.push(m.file);
    }
    return { applied: ran, alreadyApplied: applied.length };
  });
}
