import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const directory = new URL(".", import.meta.url);

export default defineConfig({
  root: fileURLToPath(directory),
  base: "./",
  publicDir: false,
  plugins: [react()],
  resolve: {
    alias: [{ find: /^\/fonts\//, replacement: fileURLToPath(new URL("../../public/fonts/", directory)) }],
  },
  server: { host: "127.0.0.1", port: 18784, strictPort: true },
  preview: { host: "127.0.0.1", port: 18784, strictPort: true },
  build: {
    outDir: fileURLToPath(new URL("../../.runtime/organization-designs/", import.meta.url)),
    emptyOutDir: true,
    rolldownOptions: {
      input: ["index.html", "team.html"].map((page) => fileURLToPath(new URL(page, directory))),
    },
  },
});
