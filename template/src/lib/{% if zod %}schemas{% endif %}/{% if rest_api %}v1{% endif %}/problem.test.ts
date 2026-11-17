import { describe, expect, it } from "vitest";
import {
  problemCodes,
  problemDetailsSchema,
  type ProblemDetails,
} from "@/lib/schemas/v1";

const valid: ProblemDetails = {
  type: "urn:problem-type:validation_failed",
  title: "The request is not valid",
  status: 422,
  code: "validation_failed",
  errors: [{ path: "title", message: "Title is required" }],
};

describe("problemDetailsSchema", () => {
  it("accepts a well-formed problem, with and without optional members", () => {
    expect(problemDetailsSchema.safeParse(valid).success).toBe(true);
    expect(
      problemDetailsSchema.safeParse({
        type: "urn:problem-type:not_found",
        title: "Not found",
        status: 404,
        code: "not_found",
      }).success,
    ).toBe(true);
  });

  it("accepts every declared code", () => {
    for (const code of problemCodes) {
      expect(
        problemDetailsSchema.safeParse({
          ...valid,
          code,
          type: `urn:problem-type:${code}`,
        }).success,
      ).toBe(true);
    }
  });

  it.each([
    ["status below 400", { status: 200 }],
    ["status above 599", { status: 600 }],
    ["a fractional status", { status: 400.5 }],
    ["an unknown code", { code: "something_else" }],
    ["a type that is not a problem URN", { type: "https://example.com/x" }],
    ["an empty title", { title: "" }],
    [
      "too many field errors",
      {
        errors: Array.from({ length: 11 }, () => ({ path: "a", message: "b" })),
      },
    ],
    [
      "an unexpected member",
      { stack: "Error: boom\n    at /home/app/secret.ts:1:1" },
    ],
  ])("rejects %s", (_label, change) => {
    expect(
      problemDetailsSchema.safeParse({ ...valid, ...change }).success,
    ).toBe(false);
  });
});
