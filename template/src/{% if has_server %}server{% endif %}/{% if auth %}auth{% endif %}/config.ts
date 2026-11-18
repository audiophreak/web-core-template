import "server-only";
import { z } from "zod";

/** Names the variables that are wrong, never their values: the secret and the URLs are credentials. */
export class AuthConfigError extends Error {
  constructor(readonly variables: readonly string[]) {
    super(
      `Invalid authentication configuration: check ${variables.join(", ")}`,
    );
    this.name = "AuthConfigError";
  }
}

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

function parseOrigin(value: string): URL | null {
  try {
    const url = new URL(value);
    const plain = url.protocol === "http:" || url.protocol === "https:";
    // An origin is scheme, host and port only: no path, query, credentials or wildcard.
    return plain && url.origin === value && !value.includes("*") ? url : null;
  } catch {
    return null;
  }
}

const isLoopback = (url: URL) => LOOPBACK_HOSTS.has(url.hostname);

const secretSchema = z
  .string()
  .min(32)
  .refine((value) => new Set(value).size >= 12, "too repetitive");

const originList = z
  .string()
  .transform((value) =>
    value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
  )
  .refine((items) => items.every((item) => parseOrigin(item) !== null));

const addressList = z
  .string()
  .transform((value) =>
    value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
  )
  .refine((items) =>
    items.every((item) => /^[0-9a-fA-F:.]+(\/\d{1,3})?$/.test(item)),
  );

const environmentSchema = z.object({
  BETTER_AUTH_SECRET: secretSchema,
  BETTER_AUTH_URL: z.string().refine((value) => parseOrigin(value) !== null),
  AUTH_TRUSTED_ORIGINS: originList.optional(),
  AUTH_ALLOW_SIGN_UP: z.enum(["true", "false"]).optional(),
  AUTH_TRUSTED_IP_HEADER: z
    .string()
    .regex(/^[a-z0-9-]{1,64}$/)
    .optional(),
  AUTH_TRUSTED_PROXIES: addressList.optional(),
  NODE_ENV: z.string().optional(),
});

export type AuthConfig = {
  readonly secret: string;
  /** The public origin of this application: where cookies are issued and where links point. */
  readonly baseURL: string;
  /** Origins allowed to make state-changing requests: the base origin plus any listed explicitly. */
  readonly trustedOrigins: readonly string[];
  readonly production: boolean;
  /** Open self-registration. Off by default in production: see docs/auth.md. */
  readonly signUpEnabled: boolean;
  readonly secureCookies: boolean;
  /** The header a trusted reverse proxy sets to the client address, if the deployment has one. */
  readonly ipHeader: string | null;
  readonly trustedProxies: readonly string[];
};

/**
 * Reads and validates the authentication settings (server side only). There is no default for the
 * secret or the URL. Origins are never inferred: only the base origin and `AUTH_TRUSTED_ORIGINS` are
 * trusted, and localhost is not added for you. In production the base URL and every extra origin
 * must be https (a loopback base URL is allowed so a production build can be tried on this machine).
 */
export function loadAuthConfig(
  env: Readonly<Record<string, string | undefined>> = process.env,
): AuthConfig {
  const parsed = environmentSchema.safeParse(env);
  if (!parsed.success) {
    throw new AuthConfigError([
      ...new Set(parsed.error.issues.map((issue) => String(issue.path[0]))),
    ]);
  }
  const data = parsed.data;
  const base = parseOrigin(data.BETTER_AUTH_URL)!;
  const production = data.NODE_ENV === "production";
  const extra = data.AUTH_TRUSTED_ORIGINS ?? [];
  const problems: string[] = [];
  if (base.protocol !== "https:" && !isLoopback(base))
    problems.push("BETTER_AUTH_URL");
  if (
    production &&
    extra.some((origin) => parseOrigin(origin)!.protocol !== "https:")
  )
    problems.push("AUTH_TRUSTED_ORIGINS");
  if (problems.length) throw new AuthConfigError(problems);
  return {
    secret: data.BETTER_AUTH_SECRET,
    baseURL: base.origin,
    trustedOrigins: [...new Set([base.origin, ...extra])],
    production,
    signUpEnabled: data.AUTH_ALLOW_SIGN_UP
      ? data.AUTH_ALLOW_SIGN_UP === "true"
      : !production,
    secureCookies: base.protocol === "https:",
    ipHeader: data.AUTH_TRUSTED_IP_HEADER ?? null,
    trustedProxies: data.AUTH_TRUSTED_PROXIES ?? [],
  };
}
