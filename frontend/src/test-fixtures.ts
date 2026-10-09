// Typed API data for tests, built from the generated types (CIS §6.9).
import type { Capabilities, Issue, ReviewResource, ReviewResult } from "./api/client";

export const capabilities: Capabilities = {
  languages: [{ id: "python", display_name: "Python", monaco_language: "python" }],
  limits: { max_source_bytes: 100, max_source_lines: 5 },
  review_timeout_seconds: 300,
  improvement_enabled: false,
};

const SUCCEEDED = { status: "SUCCEEDED", error_code: null, skip_reason: null, message: null, duration_ms: 0 } as const;

export function issue(overrides: Partial<Issue> = {}): Issue {
  return {
    issue_id: "ISS-001",
    severity: "HIGH",
    severity_source: "STATIC",
    category: "SECURITY",
    title: "Shell injection risk",
    location: { start_line: 2, end_line: 2 },
    additional_locations: [],
    occurrence_count: 1,
    summary: "The command runs through the shell.",
    impact: "Crafted input can run other commands.",
    recommendation: "Pass the arguments as a list.",
    provenance: "STATIC",
    confidence: "HIGH",
    sources: [{ origin: "bandit", rule_key: "bandit:B602" }],
    ...overrides,
  };
}

export function result(overrides: Partial<ReviewResult> = {}): ReviewResult {
  return {
    summary: { text: "One security issue was found.", source: "AI" },
    score: {
      overall: 70,
      band: "FAIR",
      provisional: false,
      assessed_weight: 100,
      categories: [
        { category: "CORRECTNESS", assessed: true, score: 100, issue_count: 0 },
        { category: "SECURITY", assessed: true, score: 80, issue_count: 1 },
        { category: "PERFORMANCE", assessed: true, score: 100, issue_count: 0 },
        { category: "READABILITY", assessed: true, score: 100, issue_count: 0 },
        { category: "MAINTAINABILITY", assessed: true, score: 100, issue_count: 0 },
        { category: "BEST_PRACTICE", assessed: true, score: 100, issue_count: 0 },
      ],
      caps_applied: [],
      policy_version: "1.0",
    },
    coverage: { complete: true, unassessed_categories: [], missing_components: [] },
    issues: [issue()],
    total_issue_count: 1,
    severity_counts: { CRITICAL: 0, HIGH: 1, MEDIUM: 0, LOW: 0 },
    issues_truncated: false,
    improved_code: {
      status: "UNAVAILABLE",
      code: null,
      notes: [],
      failure_code: null,
      message: "Improved-code generation is disabled.",
    },
    analysis: {
      static_analysis: SUCCEEDED,
      static_tools: [
        { tool: "pylint", tool_version: "4.1.2", outcome: SUCCEEDED },
        { tool: "bandit", tool_version: "1.9.4", outcome: SUCCEEDED },
      ],
      ai_analysis: SUCCEEDED,
      improvement: { ...SUCCEEDED, status: "SKIPPED", skip_reason: "DISABLED" },
    },
    capabilities: { static_analysis: true, ai_analysis: true, improved_code: false },
    warnings: [],
    metadata: {
      ai_provider: "fake",
      ai_model: "fake",
      prompt_version: "v1",
      scoring_policy_version: "1.0",
      analyzer_versions: [],
      source_bytes: 6,
      source_lines: 2,
      duration_ms: 5,
    },
    ...overrides,
  };
}

export const REVIEW_ID = "11111111-1111-4111-8111-111111111111";

export function resource(overrides: Partial<ReviewResource> = {}): ReviewResource {
  return {
    review_id: REVIEW_ID,
    status: "PENDING",
    stage: "QUEUED",
    language: "python",
    created_at: "2026-10-10T00:00:00Z",
    finished_at: null,
    result: null,
    failure: null,
    ...overrides,
  };
}

export const running = (overrides: Partial<ReviewResource> = {}) =>
  resource({ status: "RUNNING", stage: "ANALYZING", ...overrides });

export const completed = (overrides: Partial<ReviewResource> = {}) =>
  resource({ status: "COMPLETED", stage: "FINISHED", finished_at: "2026-10-10T00:00:05Z", result: result(), ...overrides });

export const failedReview = (overrides: Partial<ReviewResource> = {}) =>
  resource({
    status: "FAILED",
    stage: "FINISHED",
    finished_at: "2026-10-10T00:00:05Z",
    failure: { code: "REVIEW_TIMEOUT", message: "The review took too long and was stopped." },
    ...overrides,
  });
