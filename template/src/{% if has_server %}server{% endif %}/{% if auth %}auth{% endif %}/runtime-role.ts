import "server-only";
import { getPool } from "@/server/db/pool";
import { UnsafeRuntimeRoleError } from "./errors";

const CHECKED = Symbol.for("web-core.auth.runtime-role-checked");
type Holder = { [CHECKED]?: Promise<void> };

async function check(): Promise<void> {
  const result = await getPool().query<{ unsafe: boolean }>(
    `select (r.rolsuper or r.rolcreaterole or r.rolcreatedb or r.rolbypassrls or r.rolreplication
              or exists (select 1 from pg_namespace n where n.nspowner = r.oid)
              or exists (select 1 from pg_class c where c.relowner = r.oid)) as unsafe
       from pg_roles r where r.rolname = current_user`,
  );
  if (result.rows.length !== 1 || result.rows[0].unsafe)
    throw new UnsafeRuntimeRoleError();
}

/**
 * Refuses to run as an owner or administrator. The check is made once per process, on the first
 * request that needs the database, and fails closed: a failed check is retried and never cached as
 * success. The least-privilege role is what makes row-level security binding (an owner bypasses it).
 */
export function assertRuntimeRole(): Promise<void> {
  const holder = globalThis as Holder;
  holder[CHECKED] ??= check().catch((error: unknown) => {
    holder[CHECKED] = undefined;
    throw error;
  });
  return holder[CHECKED];
}

/** Forgets the result (tests that switch roles or databases). */
export function resetRuntimeRoleCheck(): void {
  (globalThis as Holder)[CHECKED] = undefined;
}
