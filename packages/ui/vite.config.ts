import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

export default defineConfig({
  build: {
    lib: {
      entry: {
        index: fileURLToPath(new URL("./src/index.ts", import.meta.url)),
        react: fileURLToPath(new URL("./src/react/index.tsx", import.meta.url)),
      },
      formats: ["es"],
      fileName: (_format, name) => `${name}.js`,
    },
    rolldownOptions: { external: [/^react(?:\/|$)/] },
    sourcemap: true,
  },
});
