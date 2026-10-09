import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiResult, ReviewClient, ReviewResource } from "../../api/client";
import { completed, failedReview, resource, running } from "../../test-fixtures";
import {
  CLIENT_TIMEOUT,
  LOST_CONNECTION,
  initialState,
  reviewReducer,
  useReviewSession,
  type Phase,
  type SessionEvent,
  type SessionState,
} from "./useReviewSession";

const SNAPSHOT = { language: "python", source_code: "import os\n" };
const ERROR = { code: "SERVICE_BUSY", message: "busy" };

describe("reviewReducer (§15.4)", () => {
  const at = (phase: Phase, review: ReviewResource | null = running()): SessionState => ({
    ...initialState,
    phase,
    snapshot: SNAPSHOT,
    idempotencyKey: "key-1",
    review,
  });
  const submit: SessionEvent = { type: "SUBMIT", snapshot: SNAPSHOT, idempotencyKey: "key-2" };
  const polled = (review: ReviewResource): SessionEvent => ({ type: "POLLED", resource: review });

  it("SUBMIT from idle or a terminal state starts a new submission", () => {
    for (const phase of ["idle", "completed", "partial", "failed"] as Phase[]) {
      expect(reviewReducer(at(phase), submit)).toEqual({
        ...initialState,
        phase: "submitting",
        snapshot: SNAPSHOT,
        idempotencyKey: "key-2",
      });
    }
  });

  it("ACCEPTED, for 202 and 200 alike, leads to analyzing", () => {
    const next = reviewReducer(at("submitting", null), { type: "ACCEPTED", resource: resource() });
    expect(next.phase).toBe("analyzing");
    expect(next.review).toEqual(resource());
  });

  it("REJECTED returns to idle with the error; FAILED fails", () => {
    expect(reviewReducer(at("submitting", null), { type: "REJECTED", error: ERROR })).toMatchObject({
      phase: "idle",
      error: ERROR,
    });
    for (const phase of ["submitting", "analyzing", "generating_improvement"] as Phase[]) {
      expect(reviewReducer(at(phase), { type: "FAILED", error: ERROR })).toMatchObject({ phase: "failed", error: ERROR });
    }
  });

  it("POLLED follows the review's status and stage", () => {
    const cases: [ReviewResource, Phase][] = [
      [resource(), "analyzing"],
      [running(), "analyzing"],
      [running({ stage: "GENERATING_IMPROVEMENT" }), "generating_improvement"],
      [completed(), "completed"],
      [completed({ status: "PARTIAL" }), "partial"],
      [failedReview(), "failed"],
    ];
    for (const from of ["analyzing", "generating_improvement"] as Phase[]) {
      for (const [review, phase] of cases) {
        expect(reviewReducer(at(from), polled(review)).phase).toBe(phase);
      }
    }
    expect(reviewReducer(at("analyzing"), polled(failedReview())).error).toEqual({
      code: "REVIEW_TIMEOUT",
      message: "The review took too long and was stopped.",
    });
  });

  it("ignores a late response for another review", () => {
    const state = at("analyzing");
    expect(reviewReducer(state, polled(completed({ review_id: "another" })))).toBe(state);
  });

  it("ignores transitions that are not listed", () => {
    const ignored: [Phase, SessionEvent][] = [
      ["idle", { type: "ACCEPTED", resource: resource() }],
      ["idle", polled(completed())],
      ["idle", { type: "REJECTED", error: ERROR }],
      ["idle", { type: "FAILED", error: ERROR }],
      ["submitting", submit],
      ["submitting", polled(completed())],
      ["analyzing", submit],
      ["analyzing", { type: "ACCEPTED", resource: resource() }],
      ["analyzing", { type: "REJECTED", error: ERROR }],
      ["generating_improvement", submit],
    ];
    for (const [phase, event] of ignored) {
      const state = at(phase);
      expect(reviewReducer(state, event)).toBe(state);
    }
  });

  it("a terminal state accepts only SUBMIT and RESET", () => {
    for (const phase of ["completed", "partial", "failed"] as Phase[]) {
      const state = at(phase, completed());
      for (const event of [
        { type: "ACCEPTED", resource: resource() },
        polled(completed()),
        { type: "REJECTED", error: ERROR },
        { type: "FAILED", error: ERROR },
      ] as SessionEvent[]) {
        expect(reviewReducer(state, event)).toBe(state);
      }
      expect(reviewReducer(state, { type: "RESET" })).toEqual(initialState);
      expect(reviewReducer(state, submit).phase).toBe("submitting");
    }
  });
});

type Step = ApiResult<ReviewResource> | Error | Promise<ApiResult<ReviewResource>>;
const ok = (data: ReviewResource, status = 200): ApiResult<ReviewResource> => ({ ok: true, status, data });
const rejected = (status: number, code: string): ApiResult<ReviewResource> => ({
  ok: false,
  status,
  error: { code: code as never, message: `server: ${code}`, details: null, request_id: "r-1" },
});
const noBody = (status: number): ApiResult<ReviewResource> => ({ ok: false, status, error: null });
const network = () => new TypeError("Failed to fetch");

/** Each call takes the next scripted step; the last step repeats. */
function fakeClient(submitSteps: Step[], pollSteps: Step[] = [ok(completed())]) {
  const submits: { key: string; body: string; signal: AbortSignal }[] = [];
  const polls: { reviewId: string; signal: AbortSignal }[] = [];
  const take = async (steps: Step[]) => {
    const step = steps.length > 1 ? steps.shift() : steps[0];
    if (step instanceof Error) throw step;
    return step as ApiResult<ReviewResource>;
  };
  const client: ReviewClient = {
    submit: (snapshot, key, signal) => {
      submits.push({ key, body: JSON.stringify(snapshot), signal });
      return take(submitSteps);
    },
    poll: (reviewId, signal) => {
      polls.push({ reviewId, signal });
      return take(pollSteps);
    },
  };
  return { client, submits, polls };
}

function session(client: ReviewClient, reviewTimeoutSeconds = 300) {
  const hook = renderHook(() => useReviewSession(client, reviewTimeoutSeconds));
  return {
    hook,
    state: () => hook.result.current.state,
    submit: (snapshot = SNAPSHOT) => act(async () => hook.result.current.submit(snapshot)),
  };
}

const advance = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

describe("useReviewSession (§15.5)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("reuses one key, with an identical body, on the POST retries after network errors and 5xx", async () => {
    const fake = fakeClient([network(), noBody(502), ok(resource(), 202)]);
    const s = session(fake.client);
    await s.submit();
    expect(fake.submits).toHaveLength(1);
    expect(s.state().phase).toBe("submitting");
    await advance(1000);
    expect(fake.submits).toHaveLength(2);
    await advance(2000);
    expect(fake.submits).toHaveLength(3);
    expect(s.state().phase).toBe("analyzing");
    expect(new Set(fake.submits.map((call) => call.key)).size).toBe(1);
    expect(new Set(fake.submits.map((call) => call.body))).toEqual(new Set([JSON.stringify(SNAPSHOT)]));
    expect(s.state().idempotencyKey).toBe(fake.submits[0].key);
  });

  it("fails after the POST retries are exhausted", async () => {
    for (const [step, error] of [
      [noBody(503), { code: "INTERNAL_ERROR", message: "" }],
      [network(), { code: null, message: LOST_CONNECTION }],
    ] as const) {
      const fake = fakeClient([step]);
      const s = session(fake.client);
      await s.submit();
      await advance(3000);
      expect(fake.submits).toHaveLength(3);
      expect(s.state()).toMatchObject({ phase: "failed", error });
      s.hook.unmount();
    }
  });

  it("does not retry a 5xx that carries an ErrorBody", async () => {
    const fake = fakeClient([rejected(500, "INTERNAL_ERROR")]);
    const s = session(fake.client);
    await s.submit();
    await advance(5000);
    expect(fake.submits).toHaveLength(1);
    expect(s.state()).toMatchObject({ phase: "failed", error: { code: "INTERNAL_ERROR" } });
  });

  it.each([200, 202])("a %i response leads to analyzing", async (status) => {
    const s = session(fakeClient([ok(resource(), status)]).client);
    await s.submit();
    expect(s.state().phase).toBe("analyzing");
  });

  it("maps a 4xx on POST to a validation message or a failure", async () => {
    const cases: [number, string, Phase][] = [
      [422, "EMPTY_CODE", "idle"],
      [413, "INPUT_TOO_LARGE", "idle"],
      [422, "UNSUPPORTED_LANGUAGE", "idle"],
      [400, "INVALID_REQUEST", "idle"],
      [429, "SERVICE_BUSY", "failed"],
      [422, "IDEMPOTENCY_CONFLICT", "failed"],
      [415, "UNSUPPORTED_MEDIA_TYPE", "failed"],
    ];
    for (const [status, code, phase] of cases) {
      const s = session(fakeClient([rejected(status, code)]).client);
      await s.submit();
      expect(s.state()).toMatchObject({ phase, error: { code, message: `server: ${code}` } });
      s.hook.unmount();
    }
  });

  it("polls every second through a setTimeout chain until a terminal state", async () => {
    const steps = [ok(running()), ok(running({ stage: "GENERATING_IMPROVEMENT" })), ok(completed())];
    const fake = fakeClient([ok(resource(), 202)], steps);
    const s = session(fake.client);
    await s.submit();
    await advance(999);
    expect(fake.polls).toHaveLength(0);
    await advance(1);
    expect(fake.polls).toHaveLength(1);
    expect(s.state().phase).toBe("analyzing");
    await advance(1000);
    expect(s.state().phase).toBe("generating_improvement");
    await advance(1000);
    expect(s.state().phase).toBe("completed");
    expect(s.state().review).toEqual(completed());
    await advance(10_000);
    expect(fake.polls).toHaveLength(3);
    expect(fake.polls.every((call) => call.reviewId === resource().review_id)).toBe(true);
  });

  it("ignores a poll response for another review and keeps following its own", async () => {
    const stale = completed({ review_id: "22222222-2222-4222-8222-222222222222" });
    const fake = fakeClient([ok(resource(), 202)], [ok(stale), ok(completed())]);
    const s = session(fake.client);
    await s.submit();
    await advance(1000);
    expect(s.state()).toMatchObject({ phase: "analyzing", review: resource() });
    await advance(1000);
    expect(s.state()).toMatchObject({ phase: "completed", review: completed() });
  });

  it("tolerates three consecutive network errors while polling", async () => {
    const steps = [network(), network(), network(), ok(running()), network(), network(), network(), ok(completed())];
    const s = session(fakeClient([ok(resource(), 202)], steps).client);
    await s.submit();
    await advance(8000);
    expect(s.state().phase).toBe("completed");
  });

  it("fails on the fourth consecutive polling error", async () => {
    const fake = fakeClient([ok(resource(), 202)], [network(), noBody(502), network(), network(), ok(completed())]);
    const s = session(fake.client);
    await s.submit();
    await advance(3000);
    expect(s.state().phase).toBe("analyzing");
    await advance(1000);
    expect(s.state()).toMatchObject({ phase: "failed", error: { code: null, message: LOST_CONNECTION } });
    await advance(5000);
    expect(fake.polls).toHaveLength(4);
  });

  it("fails when the review is gone while polling", async () => {
    const s = session(fakeClient([ok(resource(), 202)], [rejected(404, "REVIEW_NOT_FOUND")]).client);
    await s.submit();
    await advance(1000);
    expect(s.state()).toMatchObject({ phase: "failed", error: { code: "REVIEW_NOT_FOUND" } });
  });

  it("gives up after 2 × review_timeout_seconds + 30 s", async () => {
    const fake = fakeClient([ok(resource(), 202)], [ok(running())]);
    const s = session(fake.client, 1); // the cap is 32 s
    await s.submit();
    await advance(31_000);
    expect(s.state().phase).toBe("analyzing");
    expect(fake.polls).toHaveLength(31);
    await advance(1000);
    expect(s.state()).toMatchObject({ phase: "failed", error: { code: null, message: CLIENT_TIMEOUT } });
    expect(fake.polls).toHaveLength(31);
  });

  it("uses a new key for each new submission and for Try again", async () => {
    const fake = fakeClient([ok(resource(), 202)], [ok(failedReview()), ok(completed())]);
    const s = session(fake.client);
    await s.submit();
    await advance(1000);
    expect(s.state().phase).toBe("failed");
    await s.submit(s.state().snapshot ?? SNAPSHOT); // Try again: the same snapshot
    await advance(1000);
    expect(s.state().phase).toBe("completed");
    await s.submit({ language: "python", source_code: "x = 2\n" }); // a new Review click
    const keys = fake.submits.map((call) => call.key);
    expect(keys).toHaveLength(3);
    expect(new Set(keys).size).toBe(3);
    expect(fake.submits[1].body).toBe(fake.submits[0].body);
  });

  it("ignores Review while a submission is in progress", async () => {
    const fake = fakeClient([ok(resource(), 202)], [ok(running())]);
    const s = session(fake.client);
    await s.submit();
    await s.submit();
    expect(fake.submits).toHaveLength(1);
  });

  it("aborts on unmount and ignores the late response", async () => {
    let answer: (result: ApiResult<ReviewResource>) => void = () => {};
    const late = new Promise<ApiResult<ReviewResource>>((resolve) => (answer = resolve));
    const fake = fakeClient([ok(resource(), 202)], [late]);
    const s = session(fake.client);
    await s.submit();
    await advance(1000);
    expect(fake.polls).toHaveLength(1);
    const before = s.state();
    s.hook.unmount();
    expect(fake.polls[0].signal.aborted).toBe(true);
    await act(async () => answer(ok(running({ stage: "GENERATING_IMPROVEMENT" })))); // not terminal
    await advance(5000);
    expect(s.state()).toBe(before);
    expect(fake.polls).toHaveLength(1);
  });
});
