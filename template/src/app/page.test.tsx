import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import Home from "@/app/page";
import appInfo from "@/lib/app-info.json";

describe("Home page", () => {
  it("renders a main landmark with the app name as the top-level heading", () => {
    const html = renderToStaticMarkup(<Home />);

    expect(html).toContain("<main");
    expect(html).toMatch(new RegExp(`<h1[^>]*>${appInfo.name}</h1>`));
  });
});
