import { randomBytes } from "node:crypto";
import { closePool } from "@/server/db/pool";
import {
  createTestDatabase,
  type TestDatabase,
} from "@/server/db/test-support/database";
import { resetAuth, getAuth } from "../auth";
import { resetRuntimeRoleCheck } from "../runtime-role";

/**
 * Helpers for the real-database authentication tests. They run the application's own code against a
 * disposable database. The tests connect as the administrator and then `set role app_runtime` for the
 * whole session (`options=-c role=app_runtime`), so every statement is subject to the privileges and
 * the row level security of the real runtime role. What that cannot show, because the session user is
 * still the administrator, is a login as the role itself: tests/ in the template and the generated
 * project's `npm run db:migrate` cover that.
 */
export const ORIGIN = "http://localhost:3000";
export const PASSWORD = "correct horse battery staple";

export type RuntimeDatabase = TestDatabase & { runtimeUrl: string };

export function configureAuthEnvironment(): void {
  process.env.BETTER_AUTH_SECRET = randomBytes(32).toString("hex");
  process.env.BETTER_AUTH_URL = ORIGIN;
  delete process.env.AUTH_TRUSTED_ORIGINS;
  delete process.env.AUTH_ALLOW_SIGN_UP;
  delete process.env.AUTH_TRUSTED_IP_HEADER;
  delete process.env.AUTH_TRUSTED_PROXIES;
}

export function runtimeUrlOf(database: TestDatabase): string {
  const url = new URL(database.url);
  url.searchParams.set("options", "-c role=app_runtime");
  return url.toString();
}

/** A migrated disposable database, with the application pointed at it as the runtime role. */
export async function createRuntimeDatabase(): Promise<RuntimeDatabase> {
  const database = await createTestDatabase();
  const runtimeUrl = runtimeUrlOf(database);
  configureAuthEnvironment();
  process.env.DATABASE_RUNTIME_URL = runtimeUrl;
  await forgetApplication();
  return { ...database, runtimeUrl };
}

/** Drops the shared pool, the Better Auth instance and the role check, so the next use starts fresh. */
export async function forgetApplication(): Promise<void> {
  resetAuth();
  resetRuntimeRoleCheck();
  await closePool();
}

export type TestUser = {
  id: string;
  email: string;
  password: string;
  /** Headers carrying the session cookie, as a browser would send them. */
  headers: Headers;
  cookie: string;
};

/** `name=value` pairs of the Set-Cookie headers of a response, joined as a Cookie request header. */
export function cookieOf(response: Response): string {
  return response.headers
    .getSetCookie()
    .map((line) => line.split(";")[0])
    .filter((pair) => !pair.endsWith("="))
    .join("; ");
}

let counter = 0;

/** Registers a user through Better Auth and returns the session it signed in. */
export async function signUp(
  label = "user",
  password = PASSWORD,
): Promise<TestUser> {
  counter += 1;
  const email = `${label}-${counter}-${randomBytes(3).toString("hex")}@example.test`;
  const response = await getAuth().api.signUpEmail({
    body: { email, password, name: label },
    asResponse: true,
  });
  if (response.status !== 200)
    throw new Error(`sign-up failed with status ${response.status}`);
  const { user } = (await response.json()) as { user: { id: string } };
  const cookie = cookieOf(response);
  return {
    id: user.id,
    email,
    password,
    cookie,
    headers: new Headers({ cookie }),
  };
}

/** A Request to the application with these headers. */
export function requestTo(
  path: string,
  init: RequestInit & { user?: TestUser } = {},
): Request {
  const { user, headers, ...rest } = init;
  const merged = new Headers(headers);
  if (user) merged.set("cookie", user.cookie);
  return new Request(`${ORIGIN}${path}`, { ...rest, headers: merged });
}

export function jsonRequest(
  path: string,
  method: string,
  body: unknown,
  user?: TestUser,
): Request {
  return requestTo(path, {
    method,
    user,
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}
