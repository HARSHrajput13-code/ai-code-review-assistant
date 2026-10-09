import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(cleanup);

// Monaco cannot run in jsdom: tests replace the editor wrapper with a textarea (CIS §20.2).
vi.mock("./editor/CodeEditor", () => ({
  CodeEditor: ({ value, onChange }: { value: string; onChange: (value: string) => void }) => (
    <textarea aria-label="Code editor" value={value} onChange={(event) => onChange(event.target.value)} />
  ),
}));
