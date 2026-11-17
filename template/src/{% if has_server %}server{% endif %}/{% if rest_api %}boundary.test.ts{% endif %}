import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve, sep } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Server code stays on the server, and the schemas that clients import stay free of it. Enforced by
 * reading the source, not by convention: this is the import-graph check the architecture relies on.
 */
const SRC = join(process.cwd(), "src");
const SERVER = join(SRC, "server") + sep;
const ROUTES = join(SRC, "app", "api") + sep;
const SCHEMAS = join(SRC, "lib", "schemas") + sep;

function sourceFiles(directory: string): string[] {
  return readdirSync(directory).flatMap((name) => {
    const path = join(directory, name);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    return /\.(ts|tsx)$/.test(name) ? [path] : [];
  });
}

const FILES = sourceFiles(SRC);
const read = (file: string) => readFileSync(file, "utf8");

function resolveImport(from: string, specifier: string): string | null {
  const base = specifier.startsWith("@/")
    ? join(SRC, specifier.slice(2))
    : specifier.startsWith(".")
      ? resolve(dirname(from), specifier)
      : null;
  if (!base) return null;
  const candidates = [
    base,
    `${base}.ts`,
    `${base}.tsx`,
    join(base, "index.ts"),
    join(base, "index.tsx"),
  ];
  return candidates.find((candidate) => FILES.includes(candidate)) ?? null;
}

function specifiersOf(file: string): string[] {
  const pattern = /(?:\bfrom\s*|\bimport\s*\(?\s*)["']([^"']+)["']/g;
  return [...read(file).matchAll(pattern)].map((match) => match[1]);
}

function importsOf(file: string): string[] {
  return specifiersOf(file).flatMap(
    (specifier) => resolveImport(file, specifier) ?? [],
  );
}

const hasUseClient = (source: string) =>
  /^\s*(?:\/\/[^\n]*\n\s*)*["']use client["']/.test(source);
const isClientComponent = (file: string) => hasUseClient(read(file));
const isTest = (file: string) => /\.test\.tsx?$/.test(file);

/** True if `blocked` is reachable from `start` by following `edgesOf` (a plain graph walk). */
function reaches(
  start: string,
  edgesOf: (node: string) => string[],
  blocked: (node: string) => boolean,
): boolean {
  const seen = new Set<string>();
  const queue = [start];
  while (queue.length) {
    const node = queue.pop()!;
    if (seen.has(node)) continue;
    seen.add(node);
    if (blocked(node)) return true;
    queue.push(...edgesOf(node));
  }
  return false;
}

describe("server boundary: the check itself", () => {
  const graph: Record<string, string[]> = {
    page: ["form"],
    form: ["helper"],
    helper: ["server/db"],
    other: ["util"],
  };
  const edges = (node: string) => graph[node] ?? [];

  it("sees server code behind a chain of imports, and only when it is there", () => {
    expect(reaches("page", edges, (node) => node.startsWith("server/"))).toBe(
      true,
    );
    expect(reaches("other", edges, (node) => node.startsWith("server/"))).toBe(
      false,
    );
  });

  it("recognises a client component directive, but not a mention of it", () => {
    expect(hasUseClient('"use client";\nimport x from "y";')).toBe(true);
    expect(hasUseClient('// note\n"use client";')).toBe(true);
    expect(hasUseClient('import x from "y";\n"use client";')).toBe(false);
    expect(hasUseClient("export const text = 'use client';")).toBe(false);
  });
});

describe("server boundary", () => {
  it("finds the source files it is meant to check", () => {
    expect(FILES.some((file) => file.startsWith(SERVER))).toBe(true);
    expect(FILES.some((file) => file.startsWith(ROUTES))).toBe(true);
  });

  it("is imported only by server code and API route handlers", () => {
    const offenders = FILES.filter(
      (file) =>
        !file.startsWith(SERVER) &&
        !file.startsWith(ROUTES) &&
        importsOf(file).some((target) => target.startsWith(SERVER)),
    );
    expect(offenders.map((file) => file.replace(SRC, "src"))).toEqual([]);
  });

  it("is never reached by a client component, directly or through what it imports", () => {
    const blocked = (file: string) =>
      file.startsWith(SERVER) || specifiersOf(file).includes("next/server");
    const offenders = FILES.filter(isClientComponent).filter((file) =>
      reaches(file, importsOf, blocked),
    );
    expect(offenders.map((file) => file.replace(SRC, "src"))).toEqual([]);
  });

  it("keeps the shared schemas free of server, framework and environment access", () => {
    const schemaFiles = FILES.filter(
      (file) => file.startsWith(SCHEMAS) && !isTest(file),
    );
    expect(schemaFiles.length).toBeGreaterThan(0);
    for (const file of schemaFiles) {
      const specifiers = specifiersOf(file);
      expect(
        specifiers.filter(
          (s) =>
            s.startsWith("next") ||
            s.startsWith("node:") ||
            s.startsWith("@/server"),
        ),
        file,
      ).toEqual([]);
      expect(read(file), file).not.toMatch(/process\.env|server-only/);
    }
  });

  it("sets no permissive CORS header anywhere in the application", () => {
    const offenders = FILES.filter(
      (file) =>
        !isTest(file) && /access-control-allow-origin/i.test(read(file)),
    );
    expect(offenders).toEqual([]);
  });

  it("contains no secret-shaped strings", () => {
    const shapes = [
      /gh[pousr]_[A-Za-z0-9]{20,}/,
      /github_pat_[A-Za-z0-9_]{20,}/,
      /AKIA[0-9A-Z]{16}/,
      /-----BEGIN [A-Z ]*PRIVATE KEY-----/,
    ];
    const offenders = FILES.filter((file) =>
      shapes.some((shape) => shape.test(read(file))),
    );
    expect(offenders).toEqual([]);
  });
});
