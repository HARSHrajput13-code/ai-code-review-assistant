import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { ReviewResource } from "../../api/client";
import { capabilities, completed, issue, resource, result, running } from "../../test-fixtures";
import { DISCLAIMER, PARTIAL_NOTICE, ResultsPanel } from "./ResultsPanel";
import { initialState, type Phase, type SessionError } from "./useReviewSession";

function show(phase: Phase, review: ReviewResource | null = null, error: SessionError | null = null) {
  const onRetry = vi.fn();
  const state = { ...initialState, phase, review, error };
  render(<ResultsPanel state={state} limits={capabilities.limits} onRetry={onRetry} />);
  return onRetry;
}

it("shows an empty state while idle", () => {
  show("idle");
  expect(screen.getByText("Enter code and select Review to see the results here.")).toBeDefined();
});

it("shows progress while the review runs", () => {
  show("analyzing", resource());
  expect(screen.getByText("Queued")).toBeDefined();
  expect(screen.getByText("Analyzing code: in progress")).toBeDefined();
  expect(screen.getByText("Generating improved code: pending")).toBeDefined();
});

it("marks analysis done while the improved code is generated", () => {
  show("generating_improvement", running({ stage: "GENERATING_IMPROVEMENT" }));
  expect(screen.queryByText("Queued")).toBeNull();
  expect(screen.getByText("Analyzing code: done")).toBeDefined();
  expect(screen.getByText("Generating improved code: in progress")).toBeDefined();
});

it("renders a completed review: score, band, disclaimer, summary and issues", () => {
  show("completed", completed());
  const score = screen.getByRole("region", { name: "Score" });
  expect(within(score).getByText("70")).toBeDefined();
  expect(score.textContent).toContain("Notable concerns detected");
  expect(within(score).getAllByTestId("score-bar")).toHaveLength(6);
  expect(screen.getByText(DISCLAIMER)).toBeDefined();
  expect(screen.queryByText(/Provisional/)).toBeNull();
  expect(screen.getByText("One security issue was found.")).toBeDefined();
  expect(screen.queryByText("Generated from static analysis")).toBeNull();
  const issues = screen.getByRole("region", { name: "Issues" });
  expect(within(issues).getByText("Issues (1)")).toBeDefined();
  expect(within(issues).getByText("Critical 0 · High 1 · Medium 0 · Low 0")).toBeDefined();
  expect(within(issues).getByText("Shell injection risk")).toBeDefined();
  expect(within(issues).getByText("Line 2")).toBeDefined();
  for (const heading of ["Problem", "Why it matters", "Recommendation"]) {
    expect(within(issues).getByRole("heading", { name: heading })).toBeDefined();
  }
  expect(within(issues).getByText("Pass the arguments as a list.")).toBeDefined();
  expect(screen.queryByText(PARTIAL_NOTICE)).toBeNull();
});

it("marks a partial review as partial and keeps its score, summary and issues", () => {
  show("partial", completed({ status: "PARTIAL" }));
  expect(screen.getByRole("status").textContent).toBe(PARTIAL_NOTICE);
  expect(within(screen.getByRole("region", { name: "Score" })).getByText("70")).toBeDefined();
  expect(screen.getByText("One security issue was found.")).toBeDefined();
  expect(within(screen.getByRole("region", { name: "Issues" })).getByText("Shell injection risk")).toBeDefined();
});

it("takes partial status only from the API status, not from coverage or warnings", () => {
  const degraded = result({
    coverage: { complete: false, unassessed_categories: [], missing_components: ["ai_analysis"] },
    warnings: [{ code: "REDUCED_COVERAGE", message: "Partial result: AI analysis did not complete." }],
  });
  show("completed", completed({ result: degraded }));
  expect(screen.queryByRole("status")).toBeNull();
});

it("shows unassessed categories as Not assessed, never as a bar, with the provisional weight", () => {
  const base = result();
  const categories = base.score.categories.map((row) =>
    row.category === "READABILITY" || row.category === "PERFORMANCE" ? { ...row, assessed: false, score: null } : row,
  );
  const provisional = result({ score: { ...base.score, provisional: true, assessed_weight: 75, categories } });
  show("partial", completed({ status: "PARTIAL", result: provisional }));
  expect(screen.getByText("Provisional: based on 75% of the scoring criteria")).toBeDefined();
  const notAssessed = screen.getAllByText("Not assessed");
  expect(notAssessed).toHaveLength(2);
  expect(notAssessed[0].getAttribute("title")).toContain("AI analysis");
  expect(screen.getAllByTestId("score-bar")).toHaveLength(4);
  const readability = screen.getByText("Readability").closest("li");
  expect(readability && within(readability).queryByTestId("score-bar")).toBeNull();
});

it("states each applied cap", () => {
  const base = result();
  show("completed", completed({ result: result({ score: { ...base.score, caps_applied: ["UNPARSEABLE_SOURCE", "CRITICAL_ISSUE"] } }) }));
  expect(screen.getByText("The score is limited to 20 because the code could not be parsed.")).toBeDefined();
  expect(screen.getByText("The score is limited to 40 because a critical issue was found.")).toBeDefined();
});

it("labels a generated summary and notes a truncated issue list", () => {
  const issues = Array.from({ length: 50 }, (_, n) => issue({ issue_id: `ISS-${n + 1}`, title: `Issue ${n + 1}` }));
  const truncated = result({
    summary: { text: "Generated text.", source: "GENERATED" },
    issues,
    total_issue_count: 75,
    issues_truncated: true,
  });
  show("completed", completed({ result: truncated }));
  expect(screen.getByText("Generated from static analysis")).toBeDefined();
  expect(screen.getByText("Showing 50 of 75")).toBeDefined();
});

it("describes an empty issue list without claiming the code has no problems", () => {
  const base = result();
  show("completed", completed({ result: result({ issues: [], total_issue_count: 0, score: { ...base.score, provisional: true } }) }));
  expect(
    screen.getByText("No issues were detected by the configured analysis. Some analysis did not run, so this result is incomplete."),
  ).toBeDefined();
});

it("shows locations, occurrences and details, with the first issue expanded", () => {
  const issues = [
    issue({ location: { start_line: 3, end_line: 5 }, occurrence_count: 2, additional_locations: [{ start_line: 9, end_line: 9 }] }),
    issue({ issue_id: "ISS-002", title: "Second", location: null }),
  ];
  show("completed", completed({ result: result({ issues, total_issue_count: 2 }) }));
  expect(screen.getByText("Lines 3–5")).toBeDefined();
  expect(screen.getByText("2 occurrences")).toBeDefined();
  expect(screen.getByText("Also at: Line 9")).toBeDefined();
  expect(screen.getByText("Location not determined")).toBeDefined();
  const cards = screen.getAllByRole("listitem").filter((item) => item.querySelector("details"));
  expect(cards.map((card) => card.querySelector("details")?.open)).toEqual([true, false]);
  expect(screen.getAllByText("bandit (bandit:B602)")).toHaveLength(2);
});

it("renders server text literally, never as HTML or Markdown", () => {
  const unsafe = issue({ title: "<img src=x onerror=alert(1)>", summary: "**bold**" });
  const { container } = render(
    <ResultsPanel
      state={{ ...initialState, phase: "completed", review: completed({ result: result({ issues: [unsafe] }) }) }}
      limits={capabilities.limits}
      onRetry={() => {}}
    />,
  );
  expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeDefined();
  expect(screen.getByText("**bold**")).toBeDefined();
  expect(container.querySelector("img")).toBeNull();
  expect(container.querySelector("strong, b")).toBeNull();
});

it("shows a failure with its code, and Try again starts over", () => {
  const onRetry = show("failed", null, { code: "SERVICE_BUSY", message: "server text" });
  const alert = screen.getByRole("alert");
  expect(within(alert).getByText("Another review is still running. Please wait and try again.")).toBeDefined();
  expect(within(alert).getByText("SERVICE_BUSY")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(onRetry).toHaveBeenCalledOnce();
});

it("shows a client-side failure without a code", () => {
  show("failed", null, { code: null, message: "Lost connection to the review service." });
  expect(screen.getByRole("alert").textContent).toBe("Lost connection to the review service.Try again");
});
