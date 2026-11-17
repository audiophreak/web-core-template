import { afterEach, describe, expect, it, vi } from "vitest";
import appInfo from "@/lib/app-info.json";
import { appInfoResponseSchema } from "@/lib/schemas/v1";
import { expectProblem } from "@/server/api/expect-problem";
import { DELETE, GET, PATCH, POST, PUT } from "./route";

afterEach(() => vi.unstubAllGlobals());

describe("GET /api/v1/app-info", () => {
  it("returns the application's name and description as JSON", async () => {
    const response = GET();

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe(
      "application/json; charset=utf-8",
    );
    expect(response.headers.get("x-content-type-options")).toBe("nosniff");
    const body = appInfoResponseSchema.parse(await response.json());
    expect(body).toEqual({
      name: appInfo.name,
      description: appInfo.description,
    });
  });

  it("exposes exactly the declared fields and no CORS headers", async () => {
    const response = GET();

    expect(Object.keys(await response.json()).sort()).toEqual([
      "description",
      "name",
    ]);
    expect(
      [...response.headers.keys()].filter((name) =>
        name.startsWith("access-control-"),
      ),
    ).toEqual([]);
  });

  it("has no side effects and makes no network request", () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);

    GET();
    GET();

    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it.each([
    ["POST", POST],
    ["PUT", PUT],
    ["PATCH", PATCH],
    ["DELETE", DELETE],
  ])(
    "answers %s with a 405 problem that lists the allowed methods",
    async (_method, handler) => {
      const response = handler();

      await expectProblem(response, 405, "method_not_allowed");
      expect(response.headers.get("allow")).toBe("GET, HEAD, OPTIONS");
    },
  );
});
