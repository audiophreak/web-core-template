import { describe, expect, it } from "vitest";
import appInfo from "@/lib/app-info.json";
import { appInfoSchema, type AppInfo } from "@/lib/schemas";

describe("appInfoSchema", () => {
  it("accepts this application's own app-info.json", () => {
    expect(appInfoSchema.safeParse(appInfo).success).toBe(true);
  });

  it("returns typed data for valid input", () => {
    const parsed: AppInfo = appInfoSchema.parse({
      name: "demo-app",
      description: "A demo application",
    });
    expect(parsed).toEqual({
      name: "demo-app",
      description: "A demo application",
    });
  });

  it("trims the description", () => {
    const parsed = appInfoSchema.parse({
      name: "demo",
      description: "  padded  ",
    });
    expect(parsed.description).toBe("padded");
  });

  it.each([
    ["an empty name", { name: "", description: "ok" }],
    ["an uppercase name", { name: "Demo", description: "ok" }],
    ["a name with an underscore", { name: "my_app", description: "ok" }],
    ["a name starting with a digit", { name: "1app", description: "ok" }],
    ["a name with a trailing hyphen", { name: "app-", description: "ok" }],
    ["a name over 64 characters", { name: "a".repeat(65), description: "ok" }],
    ["a missing description", { name: "demo" }],
    ["an empty description", { name: "demo", description: "" }],
    ["a blank description", { name: "demo", description: "   " }],
    [
      "a description over 200 characters",
      { name: "demo", description: "x".repeat(201) },
    ],
    ["an unknown key", { name: "demo", description: "ok", extra: true }],
    ["a name of the wrong type", { name: 42, description: "ok" }],
    ["a non-object", "demo"],
    ["null", null],
  ])("rejects %s", (_label, input) => {
    expect(appInfoSchema.safeParse(input).success).toBe(false);
  });

  it("reports which field was rejected", () => {
    const result = appInfoSchema.safeParse({
      name: "Bad_Name",
      description: "ok",
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues.map((issue) => issue.path.join("."))).toEqual([
        "name",
      ]);
    }
  });
});
