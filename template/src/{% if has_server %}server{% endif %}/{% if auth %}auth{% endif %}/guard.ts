import "server-only";
import { guarded, problem } from "@/server/api/responses";
import { loadAuthConfig } from "./config";
import { AuthenticationError } from "./errors";
import { requirePrincipal, type Principal } from "./principal";

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

/**
 * State-changing requests that name an origin must name a trusted one. A browser always sends `Origin`
 * on a cross-site POST, so a page on another site cannot make the signed-in user's browser change data
 * (the session cookie is also SameSite=Lax, the body must be application/json, and no CORS header is
 * ever sent). Clients that are not browsers send no `Origin` and are not affected.
 */
function originAllowed(request: Request): boolean {
  if (SAFE_METHODS.has(request.method)) return true;
  const origin = request.headers.get("origin");
  return origin === null || loadAuthConfig().trustedOrigins.includes(origin);
}

/**
 * Wraps a route handler that needs a signed-in user. There is no session: a stable 401 problem,
 * decided before the handler runs and before the database is touched. Anything the handler throws
 * becomes the generic 500 problem (see `guarded`). Authorization itself is not decided here: the
 * handler passes the principal to the repository, and the database enforces it.
 */
export function withAuthentication<Context>(
  handler: (
    request: Request,
    principal: Principal,
    context: Context,
  ) => Response | Promise<Response>,
) {
  return async (request: Request, context: Context): Promise<Response> =>
    guarded(async (req) => {
      if (!originAllowed(req)) return problem("origin_not_allowed");
      try {
        return await handler(req, await requirePrincipal(req), context);
      } catch (error) {
        if (error instanceof AuthenticationError)
          return problem("unauthenticated");
        throw error;
      }
    })(request);
}
