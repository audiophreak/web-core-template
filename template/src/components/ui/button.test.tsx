import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Button } from "@/components/ui/button";

describe("Button", () => {
  it("renders a button element with its children", () => {
    const html = renderToStaticMarkup(<Button>Save</Button>);

    expect(html).toMatch(/^<button[\s>]/);
    expect(html).toContain("Save");
    expect(html).toContain('data-variant="default"');
  });

  it("applies the requested variant and passes through props", () => {
    const html = renderToStaticMarkup(
      <Button variant="destructive" disabled>
        Delete
      </Button>,
    );

    expect(html).toContain('data-variant="destructive"');
    expect(html).toContain("disabled");
  });
});
