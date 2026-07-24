import assert from "node:assert/strict";
import { readFile, stat } from "node:fs/promises";
import test from "node:test";

const projectRoot = new URL("../", import.meta.url);

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("server-renders the complete company website", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /The Mutating Company — AI systems that doubt/i);
  assert.match(html, /AI systems/);
  assert.match(html, /doubt\./);
  assert.match(html, /Hold the plasma\. Aim the power\./);
  assert.match(html, /mailto:contact@mutating\.tech/);
  assert.match(html, /https:\/\/github\.com\/mutating/);
  assert.doesNotMatch(html, /codex-preview|Your site is taking shape|Building your site/i);
});

test("keeps every site-critical asset in the local project", async () => {
  const requiredAssets = [
    "public/muta-eye-surface.webp",
    "public/muta-v2-transparent.png",
    "public/mutant-mascot-transparent.png",
    "public/mutant-mascot.jpg",
    "public/og.png",
    "public/tokamak-reactor.html",
    "public/muta-tentacles.html",
  ];

  await Promise.all(
    requiredAssets.map(async (relativePath) => {
      const file = await stat(new URL(relativePath, projectRoot));
      assert.ok(file.isFile(), `${relativePath} must be a file`);
      assert.ok(file.size > 0, `${relativePath} must not be empty`);
    }),
  );

  const [page, styles, layout, tokamak, tentacles] = await Promise.all([
    readFile(new URL("app/page.tsx", projectRoot), "utf8"),
    readFile(new URL("app/globals.css", projectRoot), "utf8"),
    readFile(new URL("app/layout.tsx", projectRoot), "utf8"),
    readFile(new URL("public/tokamak-reactor.html", projectRoot), "utf8"),
    readFile(new URL("public/muta-tentacles.html", projectRoot), "utf8"),
  ]);

  assert.match(page, /function AnimatedEyeLogo/);
  assert.match(page, /window\.location\.reload\(\)/);
  assert.match(page, /window\.setInterval\(blink, 30000\)/);
  assert.match(page, /src="\/tokamak-reactor\.html\?v=viewport-sync-2"/);
  assert.match(styles, /\.animated-eye-logo/);
  assert.match(styles, /\.containment__formula/);
  assert.match(styles, /\.contact__tentacles/);
  assert.match(layout, /The Mutating Company — AI systems that doubt/);
  assert.match(tokamak, /tokamak-state/);
  assert.match(page, /function TentacleContactBackground/);
  assert.match(tentacles, /muta-tentacles-theme/);
  assert.match(tentacles, /PRESS_IMPULSE_MULTIPLIER/);

  const localSource = `${page}\n${styles}\n${layout}\n${tokamak}\n${tentacles}`;
  assert.doesNotMatch(localSource, /\/Users\/|\/var\/folders\//);
});
