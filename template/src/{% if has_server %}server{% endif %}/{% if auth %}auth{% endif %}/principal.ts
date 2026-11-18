import "server-only";
import { getAuth } from "./auth";
import { AuthenticationError } from "./errors";

/**
 * Who is making a request, proven by a valid session. Only this module can create one: the class is
 * not exported and every instance is registered, so a caller cannot invent a user id and hand it to
 * the data layer. `userId` is read through `userIdOf`, which refuses anything not issued here.
 */
class PrincipalImpl {
  readonly #userId: string;
  constructor(userId: string) {
    this.#userId = userId;
  }
  get userId(): string {
    return this.#userId;
  }
}

export type Principal = PrincipalImpl;

const issued = new WeakSet<PrincipalImpl>();

/** The id of a principal this module issued; throws for anything else (a forged or look-alike object). */
export function userIdOf(principal: Principal): string {
  if (!issued.has(principal)) throw new AuthenticationError();
  return principal.userId;
}

/** What the server knows about a valid session. The token is deliberately not part of it. */
export type SessionView = {
  principal: Principal;
  user: { id: string; email: string; name: string };
  session: { id: string; expiresAt: Date };
};

/** The session in these request headers (cookie), or null when there is no valid one. */
export async function sessionFromHeaders(
  headers: Headers,
): Promise<SessionView | null> {
  const found = await getAuth().api.getSession({ headers });
  const id = found?.user?.id;
  if (!found || typeof id !== "string" || id.length === 0) return null;
  const principal = new PrincipalImpl(id);
  issued.add(principal);
  return {
    principal,
    user: { id, email: found.user.email, name: found.user.name },
    session: {
      id: found.session.id,
      expiresAt: new Date(found.session.expiresAt),
    },
  };
}

/** The principal for the session in these request headers, or null when there is no valid session. */
export async function principalFromHeaders(
  headers: Headers,
): Promise<Principal | null> {
  return (await sessionFromHeaders(headers))?.principal ?? null;
}

/** Like principalFromHeaders, but a missing or invalid session throws AuthenticationError. */
export async function requirePrincipal(request: Request): Promise<Principal> {
  const principal = await principalFromHeaders(request.headers);
  if (!principal) throw new AuthenticationError();
  return principal;
}
