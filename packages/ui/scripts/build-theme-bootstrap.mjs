import { build } from "vite";
import { fileURLToPath } from "node:url";

// A self-contained blocking head script, generated from the same theme code as React.
await build({
  configFile: false,
  build: {
    emptyOutDir: false,
    lib: {
      entry: fileURLToPath(new URL("../src/theme-bootstrap.ts", import.meta.url)),
      name: "TMCThemeBootstrap",
      formats: ["iife"],
      fileName: () => "theme-bootstrap.js",
    },
  },
});
