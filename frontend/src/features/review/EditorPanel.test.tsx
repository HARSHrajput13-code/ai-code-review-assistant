import { act, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { capabilities } from "../../test-fixtures";
import { EditorPanel } from "./EditorPanel";
import type { SessionError } from "./useReviewSession";

// The limits are 100 bytes and 5 lines.
function Panel({ busy = false, rejection = null, onReview = () => {} }: { busy?: boolean; rejection?: SessionError | null; onReview?: () => void }) {
  const [code, setCode] = useState("");
  const [language, setLanguage] = useState("python");
  return (
    <EditorPanel
      capabilities={capabilities}
      language={language}
      onLanguageChange={setLanguage}
      code={code}
      onCodeChange={setCode}
      busy={busy}
      rejection={rejection}
      onReview={onReview}
    />
  );
}

const type = (code: string) => fireEvent.change(screen.getByLabelText("Code editor"), { target: { value: code } });
const button = () => screen.getByRole("button");

afterEach(() => vi.useRealTimers());

it("offers the languages from the capabilities", () => {
  render(<Panel />);
  expect(screen.getByRole("combobox", { name: "Language" })).toHaveProperty("value", "python");
  expect(screen.getByRole("option", { name: "Python" })).toBeDefined();
});

it("disables Review for empty or whitespace-only code, and enables it for valid code", () => {
  const onReview = vi.fn();
  render(<Panel onReview={onReview} />);
  expect(button()).toHaveProperty("disabled", true);
  type("   \n ");
  expect(button()).toHaveProperty("disabled", true);
  type("import os\n");
  expect(button()).toHaveProperty("disabled", false);
  fireEvent.click(button());
  expect(onReview).toHaveBeenCalledOnce();
  expect(screen.queryByRole("alert")).toBeNull();
});

it("disables Review and shows the size message for oversized code", () => {
  render(<Panel />);
  type("x".repeat(101));
  expect(button()).toHaveProperty("disabled", true);
  expect(screen.getByRole("alert").textContent).toBe("The code exceeds the maximum size of 100 bytes or 5 lines.");
  type("a\nb\nc\nd\ne\nf");
  expect(button()).toHaveProperty("disabled", true);
  type("a\nb\nc\nd\ne");
  expect(button()).toHaveProperty("disabled", false);
});

it("disables Review while a review is in progress", () => {
  render(<Panel busy />);
  type("import os\n");
  expect(button()).toHaveProperty("disabled", true);
  expect(button().textContent).toBe("Reviewing…");
});

it("updates the meter 150 ms after a change, in bytes and lines", () => {
  vi.useFakeTimers();
  render(<Panel />);
  expect(screen.getByText("0 / 100 bytes · 1 / 5 lines")).toBeDefined();
  type("é\nx");
  act(() => vi.advanceTimersByTime(149));
  expect(screen.getByText("0 / 100 bytes · 1 / 5 lines")).toBeDefined();
  act(() => vi.advanceTimersByTime(1));
  expect(screen.getByText("4 / 100 bytes · 2 / 5 lines")).toBeDefined();
});

it("shows a server 4xx as a validation message", () => {
  render(<Panel rejection={{ code: "UNSUPPORTED_LANGUAGE", message: "server text" }} />);
  expect(screen.getByRole("alert").textContent).toBe("This language is not supported yet.");
});
