import { authHandler } from "@/server/auth";

// Better Auth's endpoints under /api/auth: sign-up, sign-in, sign-out, session, change-password and
// session management. CSRF and origin checks stay on (see src/server/auth/auth.ts).
export const GET = authHandler;
export const POST = authHandler;
