import { expect, it } from "vitest";
import { byteLength, lineCount } from "./inputMetrics";

it("counts UTF-8 bytes, not characters", () => {
  expect(byteLength("")).toBe(0);
  expect(byteLength("abc")).toBe(3);
  expect(byteLength("é")).toBe(2);
  expect(byteLength("€")).toBe(3);
  expect(byteLength("😀")).toBe(4);
});

it("counts lines after CRLF and CR become LF, as the server does", () => {
  expect(lineCount("")).toBe(1);
  expect(lineCount("a\nb")).toBe(2);
  expect(lineCount("a\r\nb\r\n")).toBe(3);
  expect(lineCount("a\rb")).toBe(2);
});
