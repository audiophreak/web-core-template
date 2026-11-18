import "server-only";
import { problem } from "@/server/api/responses";
import { getAuth } from "./auth";
import { assertRuntimeRole } from "./runtime-role";

/**
 * The handler behind /api/auth/*: Better Auth's own endpoints (sign-up, sign-in, sign-out, session,
 * change-password, list and revoke sessions). Anything that goes wrong becomes the generic problem:
 * a configuration or database error never reaches the response.
 */
export async function authHandler(request: Request): Promise<Response> {
  try {
    await assertRuntimeRole();
    return await getAuth().handler(request);
  } catch {
    return problem("internal_error");
  }
}
