import { defineConfig } from "@playwright/test";
import { tmpdir } from "node:os";
import { join } from "node:path";

// 使用本机 Edge，不下载额外浏览器；调试产物留在系统临时目录。
export default defineConfig({
  testDir: "./tests",
  outputDir: join(tmpdir(), "tarinmap-e2e-results"),
  workers: 1,
  timeout: 60000,
  use: {
    baseURL: process.env.TRAINMAP_TEST_URL ?? "http://127.0.0.1:8023",
    channel: "msedge",
    viewport: { width: 1440, height: 1000 },
  },
});
