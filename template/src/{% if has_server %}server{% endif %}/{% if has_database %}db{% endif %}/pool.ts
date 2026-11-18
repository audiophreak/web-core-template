import "server-only";
import { Pool, type QueryResult, type QueryResultRow } from "pg";
import { loadDatabaseConfig } from "./config";

/** Anything that can run a query: the pool, or the connection of a transaction. */
export type Queryable = {
  query: (
    text: string,
    values?: unknown[],
  ) => Promise<QueryResult<QueryResultRow>>;
};

// One pool per server process, kept on globalThis so development reloads do not leak connections.
const POOL = Symbol.for("web-core.database.pool");
type Holder = { [POOL]?: Pool };

/**
 * The shared connection pool, created on first use from the environment. Timeouts bound every wait: a
 * connection attempt, a statement, and a transaction left idle. Nothing is logged: a driver message can
 * carry the connection string.
 */
export function getPool(): Pool {
  const holder = globalThis as Holder;
  if (!holder[POOL]) {
    const config = loadDatabaseConfig();
    const pool = new Pool({
      connectionString: config.connectionString,
      max: config.poolMax,
      connectionTimeoutMillis: config.connectTimeoutMs,
      idleTimeoutMillis: 30_000,
      statement_timeout: config.statementTimeoutMs,
      query_timeout: config.statementTimeoutMs + 1_000,
      idle_in_transaction_session_timeout: 15_000,
      application_name: "web-core-app",
    });
    // An idle connection that breaks must not crash the process; the next query reconnects.
    pool.on("error", () => {});
    holder[POOL] = pool;
  }
  return holder[POOL];
}

/** Ends the pool (tests, scripts and shutdown hooks). The next getPool() creates a new one. */
export async function closePool(): Promise<void> {
  const holder = globalThis as Holder;
  const pool = holder[POOL];
  holder[POOL] = undefined;
  await pool?.end();
}
