import { parser, reboop } from "./tools/http-lint/index.mjs";

const sourceRoots = [
  "platform/web/src",
  "platform/web/public",
  "packages/ui/src",
  "packages/ui/demo",
  "site/themes/mutating/assets",
];

export default [
  {
    ignores: [
      "**/node_modules/**",
      "**/dist/**",
      "**/.runtime/**",
      "**/.venv/**",
      "**/playwright-report/**",
      "**/test-results/**",
      "**/coverage/**",
      "site/public/**",
      "site/resources/**",
      "tests/**",
      "infra/**",
    ],
  },
  {
    ...reboop.configs.recommended,
    name: "reboop/application-http",
    files: sourceRoots.map(
      (root) => `${root}/**/*.{js,mjs,cjs,jsx,ts,mts,cts,tsx}`,
    ),
    linterOptions: {
      noInlineConfig: true,
      reportUnusedDisableDirectives: "error",
    },
  },
  {
    files: sourceRoots.map((root) => `${root}/**/*.{ts,mts,cts,tsx}`),
    languageOptions: { parser },
  },
  {
    files: sourceRoots.map((root) => `${root}/**/*.{jsx,tsx}`),
    languageOptions: { parserOptions: { ecmaFeatures: { jsx: true } } },
  },
  {
    files: ["platform/web/public/**/*.js"],
    languageOptions: { sourceType: "script" },
  },
  {
    files: sourceRoots.map((root) => `${root}/**/*.cts`),
    languageOptions: { sourceType: "commonjs" },
  },
];
