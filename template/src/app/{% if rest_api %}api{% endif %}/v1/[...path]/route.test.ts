import { describe, it } from "vitest";
import { expectProblem } from "@/server/api/expect-problem";
import { DELETE, GET, PATCH, POST, PUT } from "./route";

describe("unknown /api/v1 paths", () => {
  it.each([
    ["GET", GET],
    ["POST", POST],
    ["PUT", PUT],
    ["PATCH", PATCH],
    ["DELETE", DELETE],
  ])("answer %s with the standard 404 problem", async (_method, handler) => {
    await expectProblem(handler(), 404, "not_found");
  });
});
