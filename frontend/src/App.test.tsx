import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import App from "./App.tsx";

it("renders the scaffold", () => {
  render(<App />);
  expect(screen.getByRole("heading", { name: "AI Code Review Assistant" })).toBeDefined();
});
