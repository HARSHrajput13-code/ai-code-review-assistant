import { expect, it } from "vitest";
import { errorMessage } from "./errorMessages";

const LIMITS = { max_source_bytes: 12000, max_source_lines: 500 };

it("prefers the mapped text, with the limits filled in", () => {
  expect(errorMessage("EMPTY_CODE", "server text", LIMITS)).toBe("Please enter some code to review.");
  expect(errorMessage("INPUT_TOO_LARGE", "server text", LIMITS)).toBe(
    "The code exceeds the maximum size of 12000 bytes or 500 lines.",
  );
});

it("falls back to the given message for an unknown code", () => {
  expect(errorMessage("SOMETHING_NEW", "server text", LIMITS)).toBe("server text");
});
