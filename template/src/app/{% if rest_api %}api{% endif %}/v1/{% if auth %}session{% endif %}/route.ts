import { sessionResponseSchema } from "@/lib/schemas/v1";
import {
  guarded,
  json,
  methodNotAllowed,
  problem,
} from "@/server/api/responses";
import { sessionFromHeaders } from "@/server/auth";

/** GET /api/v1/session: who the caller is and when the session ends. 401 without a valid session. */
export const GET = guarded(async (request: Request) => {
  const current = await sessionFromHeaders(request.headers);
  if (!current) return problem("unauthenticated");
  return json(sessionResponseSchema, {
    user: current.user,
    session: { expiresAt: current.session.expiresAt.toISOString() },
  });
});

const notAllowed = methodNotAllowed(["GET", "OPTIONS"]);
export const POST = notAllowed;
export const PUT = notAllowed;
export const PATCH = notAllowed;
export const DELETE = notAllowed;
