import "server-only";
import { toDatabaseError } from "./errors";
import { getPool, type Queryable } from "./pool";

/**
 * Runs `work` in one transaction on one connection: COMMIT if it returns, ROLLBACK if it throws, and the
 * connection is always handed back. Pass the `tx` it receives to every repository call that belongs to
 * the transaction; calls made with the pool instead run outside it.
 */
export async function withTransaction<T>(
  work: (tx: Queryable) => Promise<T>,
): Promise<T> {
  let client;
  try {
    client = await getPool().connect();
  } catch (error) {
    throw toDatabaseError(error);
  }
  let broken = false;
  try {
    await client.query("begin");
    const result = await work(client);
    await client.query("commit");
    return result;
  } catch (error) {
    try {
      await client.query("rollback");
    } catch {
      broken = true; // the connection is unusable: destroy it instead of returning it to the pool
    }
    throw toDatabaseError(error);
  } finally {
    client.release(broken);
  }
}
