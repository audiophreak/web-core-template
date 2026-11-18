import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, sep } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * The authentication modules are server code: each starts with `import "server-only"`, only the public
 * client module may be imported by a browser bundle, and credentials are read in one place.
 */
const SRC = join(process.cwd(), "src");
const AUTH = join(SRC, "server", "auth") + sep;

function sourceFiles(directory: string): string[] {
  return readdirSync(directory).flatMap((name) => {
    const path = join(directory, name);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return /\.(ts|tsx)$/.test(name) ? [path] : [];
  });
}

const FILES = sourceFiles(SRC);
const read = (file: string) => readFileSync(file, "utf8");
const isTest = (file: string) =>
  /\.test\.tsx?$/.test(file) || file.includes(`${sep}test-support${sep}`);
const modules = FILES.filter((file) => file.startsWith(AUTH) && !isTest(file));
const rel = (files: string[]) => files.map((file) => file.replace(SRC, "src"));

describe("server-only authentication", () => {
  it("finds the modules it is meant to guard", () => {
    expect(rel(modules)).toEqual(
      expect.arrayContaining([
        `src${sep}server${sep}auth${sep}auth.ts`,
        `src${sep}server${sep}auth${sep}scope.ts`,
        `src${sep}server${sep}auth${sep}principal.ts`,
      ]),
    );
  });

  it('starts every module with import "server-only"', () => {
    expect(
      rel(modules.filter((file) => !/^import "server-only";/.test(read(file)))),
    ).toEqual([]);
  });

  it("reads the environment only in the two configuration modules", () => {
    const readers = FILES.filter(
      (file) => !isTest(file) && /process\.env/.test(read(file)),
    );
    expect(rel(readers).sort()).toEqual(
      [
        `src${sep}server${sep}auth${sep}config.ts`,
        `src${sep}server${sep}db${sep}config.ts`,
      ].sort(),
    );
  });

  it("exposes to the browser only the Better Auth client, which imports nothing from the server", () => {
    const client = read(join(SRC, "lib", "auth-client.ts"));
    const code = client
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/\/\/.*$/gm, "");
    const specifiers = [...code.matchAll(/from\s*["']([^"']+)["']/g)].map(
      (match) => match[1],
    );
    expect(specifiers).toEqual(["better-auth/react"]);
    expect(code).not.toMatch(/process\.env|secret|DATABASE/i);
  });

  it("never lets a principal or an owner scope be constructed outside its module", () => {
    expect(read(join(AUTH, "principal.ts"))).not.toMatch(
      /export (default )?class/,
    );
    expect(read(join(AUTH, "scope.ts"))).not.toMatch(/export (default )?class/);
  });

  it("commits no authentication secret: the example file holds a placeholder at most", () => {
    const sources = FILES.filter((file) => !isTest(file))
      .map(read)
      .join("\n");
    expect(sources).not.toMatch(
      /BETTER_AUTH_SECRET\s*[:=]\s*["'`][A-Za-z0-9+/=_-]{16,}/,
    );
  });
});
