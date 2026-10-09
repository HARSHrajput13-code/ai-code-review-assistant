// @vitest-environment node
// Node's Request needs Node's AbortSignal, which the jsdom environment replaces.
import { expect, it } from "vitest";
import { completed } from "../test-fixtures";
import { createApi } from "./client";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function harness(respond: (request: Request) => Response) {
  const requests: Request[] = [];
  const fetch = async (input: RequestInfo | URL) => {
    const request = input as Request;
    requests.push(request);
    return respond(request);
  };
  return { api: createApi({ baseUrl: "http://backend.test", fetch }), requests };
}

it("posts the snapshot as JSON with the Idempotency-Key", async () => {
  const { api, requests } = harness(() => json(202, completed()));
  const snapshot = { language: "python", source_code: "x = 1\n" };
  const result = await api.submit(snapshot, "key-0123456789abcdef", new AbortController().signal);
  expect(result).toEqual({ ok: true, status: 202, data: completed() });
  const [request] = requests;
  expect(request.method).toBe("POST");
  expect(new URL(request.url).pathname).toBe("/api/v1/reviews");
  expect(request.headers.get("Content-Type")).toBe("application/json");
  expect(request.headers.get("Idempotency-Key")).toBe("key-0123456789abcdef");
  expect(await request.json()).toEqual(snapshot);
});

it("polls the review by ID", async () => {
  const { api, requests } = harness(() => json(200, completed()));
  const result = await api.poll("abc", new AbortController().signal);
  expect(result.ok).toBe(true);
  expect(new URL(requests[0].url).pathname).toBe("/api/v1/reviews/abc");
});

it("returns the ErrorBody of an error response", async () => {
  const error = { code: "EMPTY_CODE", message: "Please enter some code.", details: null, request_id: "r-1" };
  const { api } = harness(() => json(422, { error }));
  const result = await api.submit({ language: "python", source_code: " " }, "k".repeat(16), new AbortController().signal);
  expect(result).toEqual({ ok: false, status: 422, error });
});

it("reports no usable body for a 5xx without an ErrorBody", async () => {
  for (const response of [new Response("<html>Bad gateway</html>", { status: 502 }), new Response(null, { status: 503 })]) {
    const { api } = harness(() => response);
    expect(await api.poll("abc", new AbortController().signal)).toEqual({ ok: false, status: response.status, error: null });
  }
});

it("lets a network failure propagate", async () => {
  const { api } = harness(() => {
    throw new TypeError("Failed to fetch");
  });
  await expect(api.capabilities(new AbortController().signal)).rejects.toThrow("Failed to fetch");
});
