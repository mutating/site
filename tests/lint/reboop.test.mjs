import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { ESLint } from "../../tools/http-lint/index.mjs";

const root = new URL("../../", import.meta.url);
const eslint = new ESLint({
  cwd: fileURLToPath(root),
  overrideConfigFile: fileURLToPath(new URL("eslint.config.mjs", root)),
});

async function lint(code, filePath) {
  const [result] = await eslint.lintText(code, { filePath });
  assert.ok(result, `ESLint must process ${filePath}`);
  assert.equal(result.fatalErrorCount, 0, JSON.stringify(result.messages));
  return result;
}

function assertNative(result) {
  const messages = result.messages.filter(
    (message) => message.ruleId === "reboop/require-client",
  );
  assert.equal(messages.length, 1, JSON.stringify(result.messages));
  assert.equal(messages[0].severity, 2);
  assert.match(messages[0].message, /reboop/);
  assert.match(messages[0].message, /DNS/);
}

/**
Every first-party browser source root must receive the published rule. Typed
files and TSX require the real TypeScript parser, public scripts retain classic
script semantics, and Hugo's JS source permits its existing ESM imports.
Missing roots, a missing parser, or treating Hugo source as a classic script
must fail instead of silently leaving part of the application unprotected.
*/
test("detects native HTTP access in every application source root", async () => {
  const cases = [
    ["platform/web/src/probe.js", "fetch('/api');"],
    ["platform/web/src/probe.ts", "const send: typeof fetch = fetch;"],
    [
      "platform/web/src/probe.tsx",
      "export const View=()=> <button onClick={()=>fetch('/api')}/>;",
    ],
    ["platform/web/src/probe.cts", "fetch('/api');"],
    ["packages/ui/src/probe.ts", "const send: typeof fetch = fetch;"],
    [
      "packages/ui/demo/probe.tsx",
      "export const View=()=> <button onClick={()=>fetch('/api')}/>;",
    ],
    ["platform/web/public/probe.js", "var fetch;fetch('/api');"],
    [
      "site/themes/mutating/assets/js/probe.js",
      "import '@mutating/ui';fetch('/api');",
    ],
  ];
  for (const [filePath, code] of cases) {
    const result = await lint(code, filePath);
    assertNative(result);
    assert.equal(result.errorCount, 1);
    assert.equal(result.warningCount, 0);
    const acquisition = code.lastIndexOf("fetch");
    const message = result.messages[0];
    assert.equal(message.line, 1);
    assert.equal(message.column, acquisition + 1);
  }
});

/**
The guard belongs at the transport acquisition inside the existing wrapper.
Multiple ordinary wrapper calls must not produce duplicate diagnostics, and
ignoring the transport module must not hide its single original Fetch access.
*/
test("reports the original call inside a request abstraction", async () => {
  const code =
    "async function request(path:string){return fetch(path)}void request('/first');void request('/second');";
  const result = await lint(code, "platform/web/src/lib/probe.ts");
  assertNative(result);
  assert.equal(result.messages.length, 1);
  assert.equal(result.messages[0].column, code.indexOf("fetch") + 1);
});

/**
The new client's public API and genuine local symbols remain allowed. This
rejects overbroad configuration that bans reboop itself or every property or
parameter called fetch; TypeScript and JSX still have to parse successfully.
*/
test("allows reboop and lexical fetch shadows", async () => {
  const code =
    "import{createClient}from'reboop';const api=createClient({baseUrl:'https://example.test/'});void api.json('items');const helper={fetch(){return'local'}};helper.fetch();function local(fetch:(path:string)=>unknown){return fetch('/local')}export const View=()=> <span/>;";
  const result = await lint(code, "platform/web/src/probe.tsx");
  assert.deepEqual(result.messages, []);
});

/**
The checked-in config protects editor/API users as well as the CLI's
--no-inline-config flag. A local eslint-disable must not conceal a raw
transport; removing noInlineConfig makes this source incorrectly pass.
*/
test("does not allow source comments to disable the HTTP guard", async () => {
  const result = await lint(
    "/* eslint-disable reboop/require-client */\nfetch('/api');",
    "platform/web/src/probe.js",
  );
  assertNative(result);
});

/**
Generated output, runtime copies, and test transports are outside application
linting. The explicit ignores also protect editor/default ESLint invocations
from parsing generated bundles or treating native test mocks as production.
*/
test("ignores generated bundles and test transports", async () => {
  for (const path of [
    "platform/web/dist/probe.js",
    "platform/web/.runtime/probe.js",
    "packages/ui/dist/probe.js",
    "site/public/assets/probe.js",
    "site/resources/probe.js",
    "tests/platform/web/unit/probe.js",
  ]) {
    assert.equal(await eslint.isPathIgnored(path), true, path);
  }
});

/**
Installing lint tooling must not replace the build compiler. Check actual
module resolution against each application's declared compiler version and
the isolated parser toolchain's own pin, catching accidental npm hoisting
that downgrades the site's TypeScript or feeds TypeScript 7 into the parser.
*/
test("keeps build and parser TypeScript installations independent", () => {
  const metadata = (path) =>
    JSON.parse(readFileSync(new URL(path, root), "utf8"));
  const rootRequire = createRequire(new URL("package.json", root));
  const web = metadata("platform/web/package.json");
  assert.equal(
    rootRequire("typescript/package.json").version,
    web.devDependencies.typescript,
  );
  for (const path of ["platform/web", "packages/ui"]) {
    const packageURL = new URL(`${path}/package.json`, root);
    const require = createRequire(packageURL);
    assert.equal(
      require("typescript/package.json").version,
      metadata(`${path}/package.json`).devDependencies.typescript,
    );
  }
  const toolRequire = createRequire(
    new URL("tools/http-lint/package.json", root),
  );
  assert.equal(
    toolRequire("typescript/package.json").version,
    metadata("tools/http-lint/package.json").devDependencies.typescript,
  );
});
