import { cp, mkdir, writeFile } from "node:fs/promises";
import { createMutatingHomeHeader } from "../dist/index.js";

// Preserve relative URLs for both bundlers and a directory served by Hugo.
for (const directory of ["styles", "assets"]) {
  await cp(new URL(`../src/${directory}/`, import.meta.url), new URL(`../dist/${directory}/`, import.meta.url), { recursive: true });
}

// Hugo can mount these data files; both adapters share the TypeScript preset.
await mkdir(new URL("../dist/presets/", import.meta.url), { recursive: true });
for (const language of ["en", "ru"]) {
  await writeFile(new URL(`../dist/presets/${language}.json`, import.meta.url), JSON.stringify(createMutatingHomeHeader(language), null, 2) + "\n");
}
