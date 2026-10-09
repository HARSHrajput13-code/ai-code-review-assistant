import type { ErrorCode, Limits } from "../api/client";

// User text for each error code (CIS §15.8, §17.1). It is preferred over the server `message`,
// which is the fallback for an unknown code.
const MESSAGES: Record<ErrorCode, string> = {
  INVALID_REQUEST: "The request was not valid. Please check your input.",
  UNSUPPORTED_MEDIA_TYPE: "The request format is not supported.",
  EMPTY_CODE: "Please enter some code to review.",
  INPUT_TOO_LARGE: "The code exceeds the maximum size of {max_bytes} bytes or {max_lines} lines.",
  UNSUPPORTED_LANGUAGE: "This language is not supported yet.",
  IDEMPOTENCY_CONFLICT: "This submission conflicts with an earlier one. Please submit again.",
  SERVICE_BUSY: "Another review is still running. Please wait and try again.",
  REVIEW_NOT_FOUND: "This review is no longer available. Please submit the code again.",
  STATIC_ANALYSIS_FAILURE: "Static analysis could not be completed. Results may be incomplete.",
  AI_MODEL_UNAVAILABLE:
    "AI analysis is unavailable. Check that Ollama is running and the configured model is installed.",
  AI_OUTPUT_INVALID:
    "The AI returned a response that could not be validated, so AI findings are not included.",
  AI_CONTEXT_EXCEEDED:
    "The code is too large for the AI model's context window, so AI analysis or improvement was skipped.",
  IMPROVED_CODE_INVALID: "Improved code could not be validated.",
  REVIEW_TIMEOUT: "The review took too long and was stopped. Partial results are shown where available.",
  INTERNAL_ERROR: "An unexpected error occurred. Please try again.",
};

export function errorMessage(code: string, fallback: string, limits: Limits): string {
  const text = (MESSAGES as Record<string, string | undefined>)[code];
  if (text === undefined) return fallback;
  return text
    .replace("{max_bytes}", String(limits.max_source_bytes))
    .replace("{max_lines}", String(limits.max_source_lines));
}
