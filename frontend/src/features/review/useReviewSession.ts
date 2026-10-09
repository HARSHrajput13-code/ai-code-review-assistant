// The review session: a reducer for the §15.4 state machine, and the §15.5 idempotent submission
// and polling flow (D-46). Time comes only from timers, so tests drive it with fake timers.
import { useCallback, useEffect, useReducer, useRef } from "react";
import type { ApiResult, ReviewClient, ReviewResource, Snapshot } from "../../api/client";

export type Phase =
  | "idle"
  | "submitting"
  | "analyzing"
  | "generating_improvement"
  | "completed"
  | "partial"
  | "failed";

/** A rejection or failure; `code` is null for client-side failures (lost connection, client cap). */
export interface SessionError {
  code: string | null;
  message: string;
}

export interface SessionState {
  phase: Phase;
  snapshot: Snapshot | null; // what was submitted; "Try again" submits it again
  idempotencyKey: string | null;
  review: ReviewResource | null;
  error: SessionError | null;
}

export type SessionEvent =
  | { type: "SUBMIT"; snapshot: Snapshot; idempotencyKey: string }
  | { type: "ACCEPTED"; resource: ReviewResource }
  | { type: "POLLED"; resource: ReviewResource }
  | { type: "REJECTED"; error: SessionError }
  | { type: "FAILED"; error: SessionError }
  | { type: "RESET" };

export const initialState: SessionState = {
  phase: "idle",
  snapshot: null,
  idempotencyKey: null,
  review: null,
  error: null,
};

export const isBusy = (phase: Phase): boolean =>
  phase === "submitting" || phase === "analyzing" || phase === "generating_improvement";

const isRunning = (phase: Phase): boolean => phase === "analyzing" || phase === "generating_improvement";

function phaseOf(resource: ReviewResource): Phase {
  switch (resource.status) {
    case "COMPLETED":
      return "completed";
    case "PARTIAL":
      return "partial";
    case "FAILED":
      return "failed";
    default:
      return resource.stage === "GENERATING_IMPROVEMENT" ? "generating_improvement" : "analyzing";
  }
}

/** §15.4. Transitions not listed are ignored; a terminal state accepts only SUBMIT and RESET. */
export function reviewReducer(state: SessionState, event: SessionEvent): SessionState {
  switch (event.type) {
    case "SUBMIT":
      if (isBusy(state.phase)) return state;
      return { ...initialState, phase: "submitting", snapshot: event.snapshot, idempotencyKey: event.idempotencyKey };
    case "RESET":
      return initialState;
    case "ACCEPTED": // 202 and 200 alike; polling then follows the review
      return state.phase === "submitting" ? { ...state, phase: "analyzing", review: event.resource } : state;
    case "POLLED": {
      // A late response for another review is ignored.
      if (!isRunning(state.phase) || event.resource.review_id !== state.review?.review_id) return state;
      const failure = event.resource.failure;
      return {
        ...state,
        phase: phaseOf(event.resource),
        review: event.resource,
        error: failure ? { code: failure.code, message: failure.message } : null,
      };
    }
    case "REJECTED":
      return state.phase === "submitting" ? { ...state, phase: "idle", error: event.error } : state;
    case "FAILED":
      return isBusy(state.phase) ? { ...state, phase: "failed", error: event.error } : state;
  }
}

const POST_RETRY_DELAYS_MS = [1000, 2000];
const POLL_INTERVAL_MS = 1000;
const MAX_POLL_NETWORK_ERRORS = 3;
const VALIDATION_CODES = ["EMPTY_CODE", "INPUT_TOO_LARGE", "UNSUPPORTED_LANGUAGE", "INVALID_REQUEST"];
export const LOST_CONNECTION = "Lost connection to the review service.";
export const CLIENT_TIMEOUT = "The review is taking too long. Please try again.";

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(signal.reason);
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

/** POST with the same key and body on a network error or a 5xx without a usable body. */
async function post(client: ReviewClient, snapshot: Snapshot, key: string, signal: AbortSignal) {
  for (let attempt = 0; ; attempt++) {
    const last = attempt === POST_RETRY_DELAYS_MS.length;
    try {
      const result = await client.submit(snapshot, key, signal);
      if (result.ok || result.status < 500 || result.error !== null || last) return result;
    } catch (error) {
      if (signal.aborted || last) throw error;
    }
    await sleep(POST_RETRY_DELAYS_MS[attempt], signal);
  }
}

function errorOf(result: ApiResult<unknown> & { ok: false }): SessionError {
  return result.error
    ? { code: result.error.code, message: result.error.message }
    : { code: "INTERNAL_ERROR", message: "" };
}

async function run(
  client: ReviewClient,
  snapshot: Snapshot,
  key: string,
  capMs: number,
  signal: AbortSignal,
  dispatch: (event: SessionEvent) => void,
) {
  const send = (event: SessionEvent) => {
    if (!signal.aborted) dispatch(event);
  };
  const giveUpAt = Date.now() + capMs;
  try {
    const accepted = await post(client, snapshot, key, signal);
    if (!accepted.ok) {
      const error = errorOf(accepted);
      const validation = error.code !== null && VALIDATION_CODES.includes(error.code);
      send({ type: validation ? "REJECTED" : "FAILED", error });
      return;
    }
    send({ type: "ACCEPTED", resource: accepted.data });
    let networkErrors = 0;
    for (;;) {
      await sleep(POLL_INTERVAL_MS, signal); // a setTimeout chain: polls never overlap
      if (Date.now() >= giveUpAt) {
        send({ type: "FAILED", error: { code: null, message: CLIENT_TIMEOUT } });
        return;
      }
      let polled: ApiResult<ReviewResource> | null = null;
      try {
        polled = await client.poll(accepted.data.review_id, signal);
      } catch (error) {
        if (signal.aborted) throw error;
      }
      if (polled !== null && !polled.ok && polled.error !== null) {
        send({ type: "FAILED", error: errorOf(polled) }); // for example 404 REVIEW_NOT_FOUND
        return;
      }
      if (polled === null || !polled.ok) {
        networkErrors += 1; // no response, or no usable body: retried up to 3 times in a row
        if (networkErrors > MAX_POLL_NETWORK_ERRORS) {
          send({ type: "FAILED", error: { code: null, message: LOST_CONNECTION } });
          return;
        }
        continue;
      }
      networkErrors = 0;
      if (polled.data.review_id !== accepted.data.review_id) continue; // a stale response: keep polling
      send({ type: "POLLED", resource: polled.data });
      if (["COMPLETED", "PARTIAL", "FAILED"].includes(polled.data.status)) return;
    }
  } catch {
    send({ type: "FAILED", error: { code: null, message: LOST_CONNECTION } }); // dropped if aborted
  }
}

export function useReviewSession(client: ReviewClient, reviewTimeoutSeconds: number) {
  const [state, dispatch] = useReducer(reviewReducer, initialState);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => () => controller.current?.abort(), []); // cancel in-flight work on unmount

  const busy = isBusy(state.phase);
  /** One logical submission: a new key for every Review click and every "Try again". */
  const submit = useCallback(
    (snapshot: Snapshot) => {
      if (busy) return;
      controller.current?.abort();
      const current = new AbortController();
      controller.current = current;
      const key = crypto.randomUUID();
      dispatch({ type: "SUBMIT", snapshot, idempotencyKey: key });
      const capMs = (2 * reviewTimeoutSeconds + 30) * 1000; // the client cap (§15.5)
      void run(client, snapshot, key, capMs, current.signal, dispatch);
    },
    [busy, client, reviewTimeoutSeconds],
  );

  return { state, submit };
}
