import { afterEach, describe, expect, it, vi } from "vitest";
import { validateExampleResponseSchema } from "@/lib/schemas/v1";
import { expectProblem } from "@/server/api/expect-problem";
import { DELETE, GET, PATCH, POST, PUT } from "./route";

const URL = "http://localhost/api/v1/examples/validate";
const JSON_HEADERS = { "content-type": "application/json" };

afterEach(() => vi.unstubAllGlobals());

function post(
  body: BodyInit | null,
  headers: Record<string, string> = JSON_HEADERS,
) {
  return POST(new Request(URL, { method: "POST", headers, body }));
}

describe("POST /api/v1/examples/validate: success", () => {
  it("accepts the documented example and returns a bounded result", async () => {
    const response = await post(
      JSON.stringify({
        title: "Write the report",
        priority: "high",
        tags: ["work", "q4"],
      }),
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe(
      "application/json; charset=utf-8",
    );
    const body = validateExampleResponseSchema.parse(await response.json());
    expect(body).toEqual({
      valid: true,
      titleLength: 16,
      priority: "high",
      tagCount: 2,
    });
  });

  it("applies the defaults when only the title is sent", async () => {
    const response = await post(JSON.stringify({ title: "  Padded  " }));

    expect(await response.json()).toEqual({
      valid: true,
      titleLength: 6,
      priority: "normal",
      tagCount: 0,
    });
  });

  it("does not echo the submitted text", async () => {
    const response = await post(
      JSON.stringify({
        title: "ECHO-SENTINEL-TITLE",
        tags: ["echo-sentinel-tag"],
      }),
    );
    const text = await response.text();

    expect(response.status).toBe(200);
    expect(text).not.toMatch(/sentinel/i);
  });

  it("makes no network request", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);

    await post(JSON.stringify({ title: "x" }));

    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("POST /api/v1/examples/validate: invalid input", () => {
  it("reports every invalid field with its path (422 validation_failed)", async () => {
    const problem = await expectProblem(
      await post(
        JSON.stringify({
          title: "",
          priority: "urgent",
          tags: ["ok", "Not Ok"],
        }),
      ),
      422,
      "validation_failed",
    );

    expect(problem.errors?.map((error) => error.path).sort()).toEqual([
      "priority",
      "tags.1",
      "title",
    ]);
    expect(
      problem.errors?.find((error) => error.path === "title")?.message,
    ).toBe("Title is required");
  });

  it("rejects a missing title", async () => {
    const problem = await expectProblem(
      await post("{}"),
      422,
      "validation_failed",
    );

    expect(problem.errors?.map((error) => error.path)).toEqual(["title"]);
  });

  it("rejects unexpected fields without repeating their names or values", async () => {
    const response = await post(
      JSON.stringify({
        title: "x",
        isAdmin: true,
        "secret-key-name": "SECRET-VALUE",
      }),
    );
    const text = await response.clone().text();

    const problem = await expectProblem(response, 422, "validation_failed");
    expect(
      problem.errors?.every((error) => error.message === "Unexpected field"),
    ).toBe(true);
    expect(text).not.toMatch(/isAdmin|secret-key-name|SECRET-VALUE/);
  });

  it.each([
    ["null", "null"],
    ["an array", "[]"],
    ["a string", '"text"'],
    ["a number", "42"],
  ])("rejects a JSON body that is %s", async (_label, text) => {
    await expectProblem(await post(text), 422, "validation_failed");
  });

  it("does not quote invalid values back", async () => {
    const response = await post(
      JSON.stringify({
        title: 12345678,
        priority: "QUOTE-SENTINEL",
        tags: "tag-sentinel",
      }),
    );

    expect(await response.text()).not.toMatch(
      /12345678|QUOTE-SENTINEL|tag-sentinel/,
    );
  });

  it("bounds the number of reported errors", async () => {
    const response = await post(
      JSON.stringify({
        title: "x",
        tags: Array.from({ length: 50 }, () => "Bad Tag"),
      }),
    );
    const problem = await expectProblem(response, 422, "validation_failed");

    expect(problem.errors!.length).toBeLessThanOrEqual(10);
  });
});

describe("POST /api/v1/examples/validate: malformed requests", () => {
  it("rejects malformed JSON with 400 invalid_json and does not echo it", async () => {
    const response = await post("{broken-sentinel: ");
    const text = await response.clone().text();

    await expectProblem(response, 400, "invalid_json");
    expect(text).not.toContain("broken-sentinel");
  });

  it("rejects an empty body with 400", async () => {
    await expectProblem(await post(""), 400, "invalid_json");
  });

  it.each([
    ["text/plain", { "content-type": "text/plain" }],
    ["form data", { "content-type": "application/x-www-form-urlencoded" }],
    ["no content type", {}],
  ])(
    "rejects %s with 415 and says what is accepted",
    async (_label, headers) => {
      const response = await post(JSON.stringify({ title: "x" }), headers);

      await expectProblem(response.clone(), 415, "unsupported_media_type");
      expect(response.headers.get("accept-post")).toBe("application/json");
    },
  );

  it("rejects a body over 8 KiB with 413, by declared length and by actual size", async () => {
    const big = JSON.stringify({
      title: "x",
      tags: [],
      padding: "p".repeat(9 * 1024),
    });

    await expectProblem(
      await post(big, {
        ...JSON_HEADERS,
        "content-length": String(big.length),
      }),
      413,
      "payload_too_large",
    );
    await expectProblem(
      await post(big, { ...JSON_HEADERS, "content-length": "10" }),
      413,
      "payload_too_large",
    );
  });
});

describe("POST /api/v1/examples/validate: other methods", () => {
  it.each([
    ["GET", GET],
    ["PUT", PUT],
    ["PATCH", PATCH],
    ["DELETE", DELETE],
  ])("answers %s with a 405 problem", async (_method, handler) => {
    const response = handler();

    await expectProblem(response, 405, "method_not_allowed");
    expect(response.headers.get("allow")).toBe("POST, OPTIONS");
  });
});
