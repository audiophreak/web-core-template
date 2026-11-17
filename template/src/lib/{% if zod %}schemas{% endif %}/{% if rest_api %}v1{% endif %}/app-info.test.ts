import { describe, expect, it } from "vitest";
import appInfo from "@/lib/app-info.json";
import { appInfoResponseSchema } from "@/lib/schemas/v1";

describe("appInfoResponseSchema", () => {
  it("accepts this application's own metadata", () => {
    expect(appInfoResponseSchema.safeParse(appInfo).success).toBe(true);
  });

  it.each([
    ["a missing name", { description: "x" }],
    ["a missing description", { name: "x" }],
    ["an empty name", { name: "", description: "x" }],
    ["an over-long description", { name: "x", description: "d".repeat(201) }],
    ["a non-string name", { name: 1, description: "x" }],
    ["an unexpected field", { name: "x", description: "x", version: "1.0.0" }],
  ])("rejects %s", (_label, body) => {
    expect(appInfoResponseSchema.safeParse(body).success).toBe(false);
  });
});
