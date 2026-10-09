import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Api, ApiResult, Capabilities, ReviewResource } from "./api/client";
import App from "./App.tsx";
import { capabilities, completed, failedReview, resource } from "./test-fixtures";

function fakeApi(caps: Promise<ApiResult<Capabilities>>[], polls: ReviewResource[] = [completed()]) {
  const api: Api = {
    capabilities: vi.fn(() => caps.shift() ?? Promise.reject(new TypeError("Failed to fetch"))),
    submit: vi.fn(async () => ({ ok: true as const, status: 202, data: resource() })),
    poll: vi.fn(async () => ({ ok: true as const, status: 200, data: polls.length > 1 ? polls.shift()! : polls[0] })),
  };
  return api;
}
const loaded = () => Promise.resolve({ ok: true as const, status: 200, data: capabilities });
const flush = () => act(() => vi.advanceTimersByTimeAsync(0));

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

it("loads the capabilities, then shows the editor and the results side", async () => {
  render(<App api={fakeApi([loaded()])} />);
  expect(screen.getByRole("heading", { name: "AI Code Review Assistant" })).toBeDefined();
  await flush();
  expect(screen.getByLabelText("Code editor")).toBeDefined();
  expect(screen.getByRole("combobox", { name: "Language" })).toBeDefined();
  expect(screen.getByText("Enter code and select Review to see the results here.")).toBeDefined();
});

it("blocks with Backend unreachable until a retry succeeds", async () => {
  const api = fakeApi([Promise.reject(new TypeError("Failed to fetch")), loaded()]);
  render(<App api={api} />);
  await flush();
  expect(screen.getByRole("heading", { name: "Backend unreachable" })).toBeDefined();
  expect(screen.queryByLabelText("Code editor")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await flush();
  expect(screen.getByLabelText("Code editor")).toBeDefined();
  expect(api.capabilities).toHaveBeenCalledTimes(2);
});

it("submits the code, polls, and renders the review", async () => {
  const api = fakeApi([loaded()]);
  render(<App api={api} />);
  await flush();
  fireEvent.change(screen.getByLabelText("Code editor"), { target: { value: "import os\n" } });
  fireEvent.click(screen.getByRole("button", { name: "Review" }));
  await flush();
  expect(api.submit).toHaveBeenCalledWith(
    { language: "python", source_code: "import os\n" },
    expect.stringMatching(/^[0-9a-f-]{36}$/),
    expect.any(AbortSignal),
  );
  expect(screen.getByRole("button", { name: "Reviewing…" })).toHaveProperty("disabled", true);
  expect(screen.getByLabelText("Code editor")).toHaveProperty("disabled", false); // stays editable
  await act(() => vi.advanceTimersByTimeAsync(1000));
  expect(screen.getByRole("region", { name: "Score" })).toBeDefined();
  expect(screen.getByText("Shell injection risk")).toBeDefined();
  expect(screen.getByRole("button", { name: "Review" })).toHaveProperty("disabled", false);
});

it("Try again submits the same snapshot with a new key", async () => {
  const api = fakeApi([loaded()], [failedReview(), completed()]);
  render(<App api={api} />);
  await flush();
  fireEvent.change(screen.getByLabelText("Code editor"), { target: { value: "import os\n" } });
  fireEvent.click(screen.getByRole("button", { name: "Review" }));
  await flush();
  await act(() => vi.advanceTimersByTimeAsync(1000));
  expect(screen.getByRole("alert").textContent).toContain("The review took too long and was stopped.");
  fireEvent.change(screen.getByLabelText("Code editor"), { target: { value: "edited\n" } });
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await flush();
  await act(() => vi.advanceTimersByTimeAsync(1000));
  expect(screen.getByRole("region", { name: "Score" })).toBeDefined();
  const calls = vi.mocked(api.submit).mock.calls;
  expect(calls).toHaveLength(2);
  expect(calls[1][0]).toEqual({ language: "python", source_code: "import os\n" });
  expect(calls[1][1]).not.toBe(calls[0][1]);
});
