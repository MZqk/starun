import { randomBytes } from "node:crypto";
import { rmSync } from "node:fs";
import net from "node:net";
import { spawn } from "node:child_process";

function reserveFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close();
        reject(new Error("Unable to allocate an E2E port."));
        return;
      }
      const port = address.port;
      server.close((error) => {
        if (error) reject(error);
        else resolve(port);
      });
    });
  });
}

const runId =
  process.env.STARUN_E2E_RUN_ID ??
  `${process.pid}-${Date.now()}-${randomBytes(4).toString("hex")}`;
const webPort =
  process.env.STARUN_E2E_WEB_PORT ?? String(await reserveFreePort());
const apiPort =
  process.env.STARUN_E2E_API_PORT ?? String(await reserveFreePort());
const playwright = new URL("../node_modules/.bin/playwright", import.meta.url);
const child = spawn(
  playwright.pathname,
  ["test", ...process.argv.slice(2)],
  {
    stdio: "inherit",
    env: {
      ...process.env,
      STARUN_E2E_RUN_ID: runId,
      STARUN_E2E_WEB_PORT: webPort,
      STARUN_E2E_API_PORT: apiPort,
    },
  },
);

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => child.kill(signal));
}

child.on("error", (error) => {
  console.error(error);
  process.exitCode = 1;
});
child.on("exit", (code, signal) => {
  const safeRunId = runId.replace(/[^a-zA-Z0-9_-]/g, "_");
  rmSync(new URL(`../test-results/runtime/${safeRunId}`, import.meta.url), {
    recursive: true,
    force: true,
  });
  process.exitCode = signal ? 1 : (code ?? 1);
});
