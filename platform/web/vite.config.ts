import { readFileSync } from "node:fs";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const bootstrap = () => readFileSync(new URL("../../packages/ui/dist/theme-bootstrap.js", import.meta.url));
function sharedThemeBootstrap(): Plugin {
  return {
    name: "shared-theme-bootstrap",
    configureServer(server) {
      server.middlewares.use("/theme-bootstrap.js", (_request, response) => {
        response.setHeader("Content-Type", "text/javascript");
        response.end(bootstrap());
      });
    },
    generateBundle() {
      this.emitFile({ type: "asset", fileName: "theme-bootstrap.js", source: bootstrap() });
    },
    transformIndexHtml() {
      return [{ tag: "script", attrs: { src: "/theme-bootstrap.js", "data-tmc-theme-cookie-domain": "mutating.tech" }, injectTo: "head-prepend" }];
    },
  };
}

export default defineConfig({
  plugins: [react(), sharedThemeBootstrap()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // Only public UI configuration is loaded from the static dashboard server.
      "/config.js": "http://127.0.0.1:8001",
    },
  },
});
