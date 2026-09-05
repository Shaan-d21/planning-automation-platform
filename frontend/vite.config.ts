import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// const backend = "https://pushing-easily-monitors-arctic.trycloudflare.com";
const backend = "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    allowedHosts: [
      ".trycloudflare.com"
    ],
    proxy: {
      "/api": backend,
      "/app": backend,
      "/setup": backend,
      "/static": backend,
      "/login": backend,
      "/logout": backend,
      "/auth": backend
    }
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    restoreMocks: true,
    testTimeout: 15_000,
    fileParallelism: false,
    maxWorkers: 1
  }
});
