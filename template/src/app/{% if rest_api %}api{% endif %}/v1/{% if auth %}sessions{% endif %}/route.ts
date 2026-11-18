import { sessionListResponseSchema } from "@/lib/schemas/v1";
import {
  guarded,
  json,
  methodNotAllowed,
  problem,
} from "@/server/api/responses";
import { getAuth, sessionFromHeaders } from "@/server/auth";

/**
 * GET /api/v1/sessions: the caller's own sessions (for example to spot a device they do not know).
 * Tokens are never returned. To end one, use Better Auth's POST /api/auth/revoke-session.
 */
export const GET = guarded(async (request: Request) => {
  const current = await sessionFromHeaders(request.headers);
  if (!current) return problem("unauthenticated");
  const sessions = await getAuth().api.listSessions({
    headers: request.headers,
  });
  return json(sessionListResponseSchema, {
    sessions: sessions.slice(0, 100).map((session) => ({
      id: session.id,
      createdAt: new Date(session.createdAt).toISOString(),
      expiresAt: new Date(session.expiresAt).toISOString(),
      userAgent: session.userAgent ? session.userAgent.slice(0, 300) : null,
      current: session.id === current.session.id,
    })),
  });
});

const notAllowed = methodNotAllowed(["GET", "OPTIONS"]);
export const POST = notAllowed;
export const PUT = notAllowed;
export const PATCH = notAllowed;
export const DELETE = notAllowed;
