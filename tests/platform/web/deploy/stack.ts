import { test as base, expect, request } from "@playwright/test";
import { spawn, execFileSync, type ChildProcess } from "node:child_process";
import { createWriteStream } from "node:fs";
import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

export const origin = "https://127.0.0.1:18443";
export const apiURL = "http://127.0.0.1:18210/api/v1";
type Stack = {
  restartDashboard: () => Promise<void>;
  stopAPI: () => Promise<void>;
  startAPI: () => Promise<void>;
  logs: string[];
};

export const test = base.extend<{ ready: void }, { stack: Stack }>({
  ready: [
    async ({ stack }, use, testInfo) => {
      try {
        await use();
      } finally {
        for (const path of new Set(stack.logs))
          await testInfo.attach("deployment-log", {
            path,
            contentType: "text/plain",
          });
      }
    },
    { auto: true },
  ],
  stack: [
    async ({}, use) => {
      const dashboard = resolve(".");
      await mkdir(".runtime", { recursive: true });
      const runtime = await mkdtemp(resolve(".runtime", "https-"));
      const site = resolve(runtime, "site");
      await mkdir(resolve(site, "ru"), { recursive: true });
      const processes = new Set<ChildProcess>();
      const logs: string[] = [];
      const client = await request.newContext({ ignoreHTTPSErrors: true });
      function launch(
        name: string,
        command: string,
        args: string[],
        env: NodeJS.ProcessEnv = {},
      ) {
        const logPath = resolve(runtime, `${name}.log`);
        logs.push(logPath);
        const log = createWriteStream(logPath, { flags: "a" });
        const child = spawn(command, args, {
          cwd: dashboard,
          env: { ...process.env, ...env },
          stdio: ["ignore", "pipe", "pipe"],
        });
        processes.add(child);
        child.stdout!.pipe(log, { end: false });
        child.stderr!.pipe(log, { end: false });
        child.on("exit", () => {
          processes.delete(child);
          log.end();
        });
        child.on("error", (error) => log.end(String(error)));
        return child;
      }
      async function stop(child: ChildProcess) {
        if (!child.pid || child.exitCode !== null || child.signalCode !== null)
          return;
        await new Promise<void>((done) => {
          const timer = setTimeout(() => child.kill("SIGKILL"), 10_000);
          child.once("exit", () => {
            clearTimeout(timer);
            done();
          });
          child.kill("SIGTERM");
        });
      }
      async function ready(url: string) {
        await expect
          .poll(
            async () => {
              try {
                return (await client.get(url, { timeout: 1000 })).status();
              } catch {
                return 0;
              }
            },
            { timeout: 20_000 },
          )
          .toBe(200);
      }
      const apiEnv = {
        THRONG_DATABASE_URL: `sqlite+aiosqlite:///${runtime}/api.db`,
        THRONG_STORAGE_PATH: resolve(runtime, "artifacts"),
        THRONG_ALLOW_REGISTRATION: "true",
      };
      let api: ChildProcess;
      let bff: ChildProcess;
      async function startAPI() {
        api = launch(
          "api",
          "../api/.venv/bin/python",
          [
            "-m",
            "uvicorn",
            "throng_api.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            "18210",
          ],
          apiEnv,
        );
        await ready("http://127.0.0.1:18210/health/ready");
      }
      async function startDashboard() {
        bff = launch(
          "dashboard",
          ".venv/bin/python",
          [
            "-m",
            "uvicorn",
            "server.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            "18211",
          ],
          {
            DASHBOARD_API_URL: "http://127.0.0.1:18210",
            DASHBOARD_PUBLIC_ORIGIN: origin,
            DASHBOARD_SECURE_COOKIE: "true",
            DASHBOARD_SESSION_KEY: Buffer.alloc(32, 19).toString("base64"),
          },
        );
        await ready("http://127.0.0.1:18211/account/login");
      }
      try {
        if (process.env.DASHBOARD_SITE_SOURCE) {
          execFileSync("hugo", [
            "--source",
            resolve(process.env.DASHBOARD_SITE_SOURCE),
            "--destination",
            site,
            "--baseURL",
            `${origin}/`,
          ]);
        } else {
          // CI owns only this repository. This static page represents the other nginx location.
          const html =
            '<!doctype html><html><head><title>The Mutating Company</title></head><body><h1>The Mutating Company</h1><a href="/account/">Account</a></body></html>';
          await writeFile(resolve(site, "index.html"), html);
          await writeFile(resolve(site, "ru/index.html"), html);
        }
        execFileSync(
          "../api/.venv/bin/python",
          ["-m", "throng_api.cli", "migrate"],
          { env: { ...process.env, ...apiEnv } },
        );
        execFileSync(
          "openssl",
          [
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-keyout",
            resolve(runtime, "key.pem"),
            "-out",
            resolve(runtime, "cert.pem"),
          ],
          { stdio: "ignore" },
        );
        // Test the shipped deployment fragment; substitute only the private test upstream port.
        const locations = (await readFile("deploy/nginx.conf", "utf8")).replace(
          "127.0.0.1:8001",
          "127.0.0.1:18211",
        );
        const config = `pid "${runtime}/nginx.pid";
error_log "${runtime}/nginx-error.log";
events { worker_connections 128; }
http {
  access_log "${runtime}/nginx-access.log";
  client_body_temp_path "${runtime}/body";
  proxy_temp_path "${runtime}/proxy";
  types { text/html html; text/css css; application/javascript js; image/svg+xml svg; image/png png; font/woff2 woff2; }
  server {
    listen 127.0.0.1:18443 ssl;
    server_name localhost;
    ssl_certificate "${runtime}/cert.pem";
    ssl_certificate_key "${runtime}/key.pem";
    root "${site}";
    ${locations}
    location / { try_files $uri $uri/ =404; }
  }
}`;
        const configPath = resolve(runtime, "nginx.conf");
        await writeFile(configPath, config);
        await startAPI();
        await startDashboard();
        launch("nginx", process.env.NGINX_EXECUTABLE || "nginx", [
          "-p",
          `${runtime}/`,
          "-c",
          configPath,
          "-g",
          "daemon off;",
        ]);
        await ready(`${origin}/account/login`);
        await use({
          restartDashboard: async () => {
            await stop(bff);
            await startDashboard();
          },
          stopAPI: () => stop(api),
          startAPI,
          logs,
        });
      } finally {
        await Promise.all([...processes].map(stop));
        await client.dispose();
      }
    },
    { scope: "worker" },
  ],
});
export { expect };
