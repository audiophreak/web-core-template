import { expect } from "vitest";
import { problemDetailsSchema } from "@/lib/schemas/v1";

// Anything that would reveal internals: stack frames, file paths, environment access.
const INTERNALS =
  /stack|node_modules|\/Users\/|\/home\/|\.tsx?:\d|process\.env|at \S+ \(/;

/** Asserts the RFC 9457 shape, status, stable code and media type of a problem response; returns the body. */
export async function expectProblem(
  response: Response,
  status: number,
  code: string,
) {
  const text = await response.text();
  expect(response.status).toBe(status);
  expect(response.headers.get("content-type")).toBe(
    "application/problem+json; charset=utf-8",
  );
  const body = problemDetailsSchema.parse(JSON.parse(text));
  expect(body).toMatchObject({
    code,
    status,
    type: `urn:problem-type:${code}`,
  });
  expect(text).not.toMatch(INTERNALS);
  return body;
}
