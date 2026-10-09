// Typed access to the backend API. Every request and response type comes from the generated
// OpenAPI types (CIS §6.9); none is written by hand.
import createClient from "openapi-fetch";
import type { components, paths } from "./generated/schema";

type Schemas = components["schemas"];
export type ApiError = Schemas["ErrorInfo"];
export type Capabilities = Schemas["Capabilities"];
export type Category = Schemas["Category"];
export type ErrorCode = Schemas["ErrorCode"];
export type Issue = Schemas["IssueDTO"];
export type Limits = Schemas["LimitsDTO"];
export type ReviewResource = Schemas["ReviewResource"];
export type ReviewResult = Schemas["ReviewResultDTO"];
export type ScoreBand = Schemas["ScoreBand"];
export type ScoreCap = Schemas["ScoreCap"];
export type Severity = Schemas["Severity"];
export type Snapshot = Schemas["ReviewCreateRequest"];

/** A response: the data, or the §6.6 error when the body is a usable `ErrorBody` (else null). */
export type ApiResult<T> =
  | { ok: true; status: number; data: T }
  | { ok: false; status: number; error: ApiError | null };

export interface ReviewClient {
  submit(snapshot: Snapshot, idempotencyKey: string, signal: AbortSignal): Promise<ApiResult<ReviewResource>>;
  poll(reviewId: string, signal: AbortSignal): Promise<ApiResult<ReviewResource>>;
}

export interface Api extends ReviewClient {
  capabilities(signal: AbortSignal): Promise<ApiResult<Capabilities>>;
}

function toResult<T>(response: Response, data: T | undefined, error: unknown): ApiResult<T> {
  if (response.ok && data !== undefined) return { ok: true, status: response.status, data };
  const usable =
    typeof error === "object" && error !== null && "error" in error ? (error as Schemas["ErrorBody"]).error : null;
  return { ok: false, status: response.status, error: usable };
}

/** Same-origin by default: the Vite dev and preview servers proxy /api to the backend. */
export function createApi(options: { baseUrl?: string; fetch?: typeof globalThis.fetch } = {}): Api {
  const client = createClient<paths>({
    baseUrl: options.baseUrl,
    fetch: options.fetch ?? ((request) => globalThis.fetch(request)),
  });
  return {
    async capabilities(signal) {
      const { response, data, error } = await client.GET("/api/v1/capabilities", { signal });
      return toResult(response, data, error);
    },
    async submit(snapshot, idempotencyKey, signal) {
      const { response, data, error } = await client.POST("/api/v1/reviews", {
        body: snapshot,
        params: { header: { "Idempotency-Key": idempotencyKey } },
        signal,
      });
      return toResult(response, data, error);
    },
    async poll(reviewId, signal) {
      const { response, data, error } = await client.GET("/api/v1/reviews/{review_id}", {
        params: { path: { review_id: reviewId } },
        signal,
      });
      return toResult(response, data, error);
    },
  };
}
