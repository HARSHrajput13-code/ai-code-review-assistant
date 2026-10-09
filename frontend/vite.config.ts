import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The dev and preview servers proxy /api to the backend, so requests stay same-origin (CIS §2.1, §16.2).
const proxy = {
  "/api": { target: process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8000" },
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { host: "127.0.0.1", port: 5173, strictPort: true, proxy },
  preview: { host: "127.0.0.1", port: 5173, strictPort: true, proxy },
  test: { environment: "jsdom", setupFiles: ["./src/test-setup.tsx"] },
});
