import { defineConfig } from "vitest/config";
export default defineConfig({ test: { include: ["../../tests/platform/web/unit/**/*.test.ts"] } });
