import { describe, expect, it } from "vitest";
import { AuthConfigError, loadAuthConfig } from "./config";

const SECRET = "0123456789abcdef0123456789abcdef0123456789abcdef";
const valid = {
  BETTER_AUTH_SECRET: SECRET,
  BETTER_AUTH_URL: "http://localhost:3000",
};

function names(env: Record<string, string>): string[] {
  try {
    loadAuthConfig(env);
  } catch (error) {
    if (error instanceof AuthConfigError) return [...error.variables];
    throw error;
  }
  return [];
}

describe("loadAuthConfig", () => {
  it("accepts the local development setup", () => {
    expect(loadAuthConfig(valid)).toEqual({
      secret: SECRET,
      baseURL: "http://localhost:3000",
      trustedOrigins: ["http://localhost:3000"],
      production: false,
      signUpEnabled: true,
      secureCookies: false,
      ipHeader: null,
      trustedProxies: [],
    });
  });

  it("trusts only the base origin and the origins listed on purpose, without duplicates", () => {
    const config = loadAuthConfig({
      ...valid,
      BETTER_AUTH_URL: "https://app.example.com",
      AUTH_TRUSTED_ORIGINS:
        "https://admin.example.com, https://app.example.com",
      NODE_ENV: "production",
    });
    expect(config.trustedOrigins).toEqual([
      "https://app.example.com",
      "https://admin.example.com",
    ]);
    expect(config.secureCookies).toBe(true);
  });

  it("does not trust localhost in production unless the application itself is served from it", () => {
    const config = loadAuthConfig({
      ...valid,
      BETTER_AUTH_URL: "https://app.example.com",
      NODE_ENV: "production",
    });
    expect(config.trustedOrigins).toEqual(["https://app.example.com"]);
    expect(
      names({
        ...valid,
        BETTER_AUTH_URL: "https://app.example.com",
        AUTH_TRUSTED_ORIGINS: "http://localhost:3000",
        NODE_ENV: "production",
      }),
    ).toEqual(["AUTH_TRUSTED_ORIGINS"]);
  });

  it("turns open sign-up off in production by default and lets a deployment choose", () => {
    const production = {
      ...valid,
      BETTER_AUTH_URL: "https://app.example.com",
      NODE_ENV: "production",
    };
    expect(loadAuthConfig(production).signUpEnabled).toBe(false);
    expect(
      loadAuthConfig({ ...production, AUTH_ALLOW_SIGN_UP: "true" })
        .signUpEnabled,
    ).toBe(true);
    expect(
      loadAuthConfig({ ...valid, AUTH_ALLOW_SIGN_UP: "false" }).signUpEnabled,
    ).toBe(false);
  });

  it("reads the trusted proxy settings", () => {
    const config = loadAuthConfig({
      ...valid,
      AUTH_TRUSTED_IP_HEADER: "x-real-ip",
      AUTH_TRUSTED_PROXIES: "10.0.0.0/8, ::1",
    });
    expect([config.ipHeader, config.trustedProxies]).toEqual([
      "x-real-ip",
      ["10.0.0.0/8", "::1"],
    ]);
  });

  it.each([
    [
      "a missing secret",
      { BETTER_AUTH_URL: valid.BETTER_AUTH_URL },
      ["BETTER_AUTH_SECRET"],
    ],
    [
      "a short secret",
      { ...valid, BETTER_AUTH_SECRET: "short" },
      ["BETTER_AUTH_SECRET"],
    ],
    [
      "a repetitive secret",
      { ...valid, BETTER_AUTH_SECRET: "a".repeat(64) },
      ["BETTER_AUTH_SECRET"],
    ],
    [
      "the placeholder from .env.example",
      { ...valid, BETTER_AUTH_SECRET: "generated-by-npm-run-db-init" },
      ["BETTER_AUTH_SECRET"],
    ],
    ["a missing URL", { BETTER_AUTH_SECRET: SECRET }, ["BETTER_AUTH_URL"]],
    [
      "a URL with a path",
      { ...valid, BETTER_AUTH_URL: "http://localhost:3000/app" },
      ["BETTER_AUTH_URL"],
    ],
    [
      "a URL with credentials",
      { ...valid, BETTER_AUTH_URL: "http://user:pw@localhost:3000" },
      ["BETTER_AUTH_URL"],
    ],
    [
      "a wildcard URL",
      { ...valid, BETTER_AUTH_URL: "https://*.example.com" },
      ["BETTER_AUTH_URL"],
    ],
    [
      "a non-http scheme",
      { ...valid, BETTER_AUTH_URL: "ftp://example.com" },
      ["BETTER_AUTH_URL"],
    ],
    [
      "plain http for a real host",
      { ...valid, BETTER_AUTH_URL: "http://app.example.com" },
      ["BETTER_AUTH_URL"],
    ],
    [
      "a wildcard trusted origin",
      { ...valid, AUTH_TRUSTED_ORIGINS: "https://*.example.com" },
      ["AUTH_TRUSTED_ORIGINS"],
    ],
    [
      "a bare wildcard",
      { ...valid, AUTH_TRUSTED_ORIGINS: "*" },
      ["AUTH_TRUSTED_ORIGINS"],
    ],
    [
      "a trusted origin with a path",
      { ...valid, AUTH_TRUSTED_ORIGINS: "https://a.example.com/x" },
      ["AUTH_TRUSTED_ORIGINS"],
    ],
    [
      "an invalid sign-up flag",
      { ...valid, AUTH_ALLOW_SIGN_UP: "yes" },
      ["AUTH_ALLOW_SIGN_UP"],
    ],
    [
      "an invalid header name",
      { ...valid, AUTH_TRUSTED_IP_HEADER: "x forwarded" },
      ["AUTH_TRUSTED_IP_HEADER"],
    ],
    [
      "an invalid proxy address",
      { ...valid, AUTH_TRUSTED_PROXIES: "not-an-ip" },
      ["AUTH_TRUSTED_PROXIES"],
    ],
  ])("rejects %s, naming only the variable", (_label, env, variables) => {
    expect(names(env as Record<string, string>)).toEqual(variables);
  });

  it("never puts a value into the error", () => {
    try {
      loadAuthConfig({
        ...valid,
        BETTER_AUTH_SECRET: "s3cr3t",
        BETTER_AUTH_URL: "http://user:pw-sentinel@localhost",
      });
      throw new Error("expected an error");
    } catch (error) {
      expect((error as Error).message).not.toMatch(/s3cr3t|pw-sentinel/);
    }
  });
});
