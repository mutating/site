import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8001",
      // Keep the agent API and its registration/dashboard links on one origin.
      "/v1": "http://127.0.0.1:8000",
      "/agent/v1": "http://127.0.0.1:8000",
    },
  },
});
