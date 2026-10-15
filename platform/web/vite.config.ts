import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // Only public UI configuration is loaded from the static dashboard server.
      "/config.js": "http://127.0.0.1:8001",
    },
  },
});
