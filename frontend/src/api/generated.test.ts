// @vitest-environment node
import { readFileSync } from "node:fs";
import openapiTS, { COMMENT_HEADER, astToString } from "openapi-typescript";
import { expect, it } from "vitest";

const lf = (text: string) => text.replace(/\r\n/g, "\n");

// The API types are generated, never hand-edited, and follow the committed snapshot (CIS §6.9).
it("generated/schema.d.ts is what `npm run generate:api` produces from the OpenAPI snapshot", async () => {
  const snapshot = new URL("../../../shared/openapi/openapi.json", import.meta.url);
  const expected = COMMENT_HEADER + astToString(await openapiTS(snapshot));
  const committed = readFileSync(new URL("./generated/schema.d.ts", import.meta.url), "utf8");
  expect(lf(committed)).toBe(lf(expected));
});
