import { describe, expect, it } from "vitest";
import indexHtml from "../index.html?raw";

// Frontend security conventions (CIS §15.7, §18): no remote assets, no raw HTML rendering.
const sources = import.meta.glob<string>(["./**/*.{ts,tsx,css}", "!./**/*.test.{ts,tsx}"], {
  query: "?raw",
  import: "default",
  eager: true,
});
const files: [string, string][] = [["index.html", indexHtml], ...Object.entries(sources)];

describe("frontend security conventions", () => {
  it("scans the application sources", () => {
    expect(Object.keys(sources)).toContain("./App.tsx");
  });

  it.each(files)("%s loads no remote assets", (_file, text) => {
    expect(text).not.toMatch(/https?:\/\//i);
    expect(text).not.toMatch(/["'(]\/\/[^/\s]/);
  });

  it.each(files)("%s does not use dangerouslySetInnerHTML", (_file, text) => {
    expect(text).not.toContain("dangerouslySetInnerHTML");
  });
});
