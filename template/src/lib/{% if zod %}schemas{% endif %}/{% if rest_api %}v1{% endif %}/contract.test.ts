import { describe, expect, it } from "vitest";
import { z } from "zod";
import {
  appInfoResponseSchema,
  problemCodes,
  problemDetailsSchema,
  validateExampleRequestSchema,
  validateExampleResponseSchema,
} from "@/lib/schemas/v1";

/**
 * The v1 contract, pinned. Within v1 changes are additive: a new optional field or error code means
 * extending these lists in the same change. Removing or renaming anything, or making a field
 * required, breaks callers that cannot be updated (a native app) and needs a new API version.
 */
describe("API v1 contract", () => {
  it("keeps the error codes (only additions are allowed)", () => {
    expect([...problemCodes]).toEqual([
      "invalid_json",
      "payload_too_large",
      "unsupported_media_type",
      "validation_failed",
      "method_not_allowed",
      "not_found",
      "internal_error",
    ]);
  });

  it.each([
    [
      "problem details",
      problemDetailsSchema,
      ["type", "title", "status", "detail", "code", "errors"],
    ],
    ["app-info response", appInfoResponseSchema, ["name", "description"]],
    [
      "validate request",
      validateExampleRequestSchema,
      ["title", "priority", "tags"],
    ],
    [
      "validate response",
      validateExampleResponseSchema,
      ["valid", "titleLength", "priority", "tagCount"],
    ],
  ])("keeps the fields of the %s", (_name, schema, fields) => {
    expect(Object.keys(schema.shape)).toEqual(fields);
  });

  it("keeps required request fields minimal: only the title is required", () => {
    expect(validateExampleRequestSchema.safeParse({ title: "x" }).success).toBe(
      true,
    );
    expect(validateExampleRequestSchema.safeParse({}).success).toBe(false);
  });

  it("is made only of JSON-compatible schemas, so a JSON Schema or OpenAPI document can be derived", () => {
    for (const schema of [
      problemDetailsSchema,
      appInfoResponseSchema,
      validateExampleRequestSchema,
      validateExampleResponseSchema,
    ]) {
      expect(() => JSON.stringify(z.toJSONSchema(schema))).not.toThrow();
    }
  });
});
