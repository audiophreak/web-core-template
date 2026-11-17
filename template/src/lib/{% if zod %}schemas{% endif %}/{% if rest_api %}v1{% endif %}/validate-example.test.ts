import { describe, expect, expectTypeOf, it } from "vitest";
import {
  validateExampleRequestSchema,
  validateExampleResponseSchema,
  type ValidateExampleResponse,
} from "@/lib/schemas/v1";

describe("validateExampleRequestSchema", () => {
  it("accepts the documented example and applies defaults", () => {
    expect(
      validateExampleRequestSchema.parse({
        title: "Write the report",
        priority: "high",
        tags: ["work", "q4"],
      }),
    ).toEqual({
      title: "Write the report",
      priority: "high",
      tags: ["work", "q4"],
    });
    expect(validateExampleRequestSchema.parse({ title: "  Padded  " })).toEqual(
      {
        title: "Padded",
        priority: "normal",
        tags: [],
      },
    );
  });

  it.each([
    ["a missing title", {}],
    ["an empty title", { title: "   " }],
    ["a title over 100 characters", { title: "t".repeat(101) }],
    ["a non-string title", { title: 7 }],
    ["an unknown priority", { title: "x", priority: "urgent" }],
    ["tags that are not a list", { title: "x", tags: "work" }],
    ["too many tags", { title: "x", tags: ["a", "b", "c", "d", "e", "f"] }],
    ["an empty tag", { title: "x", tags: [""] }],
    ["an upper-case tag", { title: "x", tags: ["Work"] }],
    ["a tag with a space", { title: "x", tags: ["two words"] }],
    ["a tag over 20 characters", { title: "x", tags: ["a".repeat(21)] }],
    ["an unexpected field", { title: "x", admin: true }],
    ["null", null],
    ["an array", []],
  ])("rejects %s", (_label, body) => {
    expect(validateExampleRequestSchema.safeParse(body).success).toBe(false);
  });
});

describe("validateExampleResponseSchema", () => {
  it("accepts the bounded result and infers its type", () => {
    const result: ValidateExampleResponse = validateExampleResponseSchema.parse(
      {
        valid: true,
        titleLength: 3,
        priority: "low",
        tagCount: 0,
      },
    );
    expect(result.valid).toBe(true);
    expectTypeOf(result.priority).toEqualTypeOf<"low" | "normal" | "high">();
  });

  it.each([
    [
      "valid: false",
      { valid: false, titleLength: 3, priority: "low", tagCount: 0 },
    ],
    [
      "an echoed title",
      {
        valid: true,
        titleLength: 3,
        priority: "low",
        tagCount: 0,
        title: "secret",
      },
    ],
    [
      "an out-of-range count",
      { valid: true, titleLength: 3, priority: "low", tagCount: 6 },
    ],
    ["a missing member", { valid: true, titleLength: 3, priority: "low" }],
  ])("rejects %s", (_label, body) => {
    expect(validateExampleResponseSchema.safeParse(body).success).toBe(false);
  });
});
