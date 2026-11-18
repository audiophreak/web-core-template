import "server-only";
import { toDatabaseError } from "@/server/db/errors";
import type { Queryable } from "@/server/db/pool";
import { withTransaction } from "@/server/db/transaction";
import { UnsafeRuntimeRoleError } from "./errors";
import { userIdOf, type Principal } from "./principal";
import { assertRuntimeRole } from "./runtime-role";

/**
 * The only way to reach protected data: a transaction in which the database itself knows who is asking.
 * The user id comes from a principal issued by a valid session, never from the caller, and it is set
 * with `set_config(..., true)`, which lasts for this transaction only. Row-level security policies
 * read it (`app.current_user_id()`), so a query that forgets its `owner_id` filter still sees only
 * the caller's rows, and a pooled connection cannot carry one user's identity into the next request.
 */
class OwnerScopeImpl {
  readonly #userId: string;
  readonly db: Queryable;
  constructor(userId: string, db: Queryable) {
    this.#userId = userId;
    this.db = db;
  }
  get userId(): string {
    return this.#userId;
  }
}

export type OwnerScope = OwnerScopeImpl;

/** Runs `work` in one transaction scoped to `principal`: COMMIT on return, ROLLBACK on a throw. */
export async function withPrincipal<T>(
  principal: Principal,
  work: (scope: OwnerScope) => Promise<T>,
): Promise<T> {
  const userId = userIdOf(principal);
  try {
    await assertRuntimeRole();
  } catch (error) {
    if (error instanceof UnsafeRuntimeRoleError) throw error;
    throw toDatabaseError(error);
  }
  return withTransaction(async (tx) => {
    await tx.query("select set_config('app.user_id', $1, true)", [userId]);
    return work(new OwnerScopeImpl(userId, tx));
  });
}
