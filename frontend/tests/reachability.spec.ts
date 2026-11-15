import { test, expect, type Page } from "@playwright/test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { execFileSync } from "node:child_process";

// 这些验收测试使用已下载的真实快照，不触发全量下载；临时导出物统一清理。
async function calculate(page: Page): Promise<void> {
  const response = await page.request.get("/api/data-status");
  const status = await response.json();
  expect(status.source_kind).toBe("external");
  await page.goto("/");
  await expect(page.getByRole("button", { name: "每周时段", exact: true })).toHaveClass("active");
  await expect(page.locator(".station-tags button").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "自动", exact: true })).toHaveClass(/active/);
  await page.getByRole("button", { name: "具体日期", exact: true }).click();
  // 固定在当前快照有效期内，避免测试随系统日期过期。
  const day = status.valid_from;
  await page.getByLabel("最早出发", { exact: true }).fill(`${day}T08:00`);
  await page.getByLabel("最晚到达", { exact: true }).fill(`${day}T23:00`);
  await page.getByRole("switch").check();
  await page.getByRole("button", { name: "计算铁路可达范围", exact: true }).click();
  await expect(page.locator(".city-card").first()).toBeAttached({ timeout: 30000 });
  await expect(page.getByRole("alert")).toHaveCount(0);
}

test("真实查询、两侧收起、全屏和历史恢复", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await calculate(page);
  const summary = await page.locator(".result-toolbar p").innerText();
  await page.getByRole("button", { name: "收起查询条件", exact: true }).click();
  await expect(page.locator(".control-panel")).toHaveClass(/collapsed/);
  await page.getByRole("button", { name: "收起地点列表", exact: true }).click();
  await expect(page.locator(".destination-panel")).toHaveClass(/collapsed/);
  await page.getByRole("button", { name: "打开地图全屏", exact: true }).click();
  await expect(page.locator(".map-frame")).toHaveClass(/is-fullscreen/);
  await page.keyboard.press("Escape");
  await expect(page.locator(".map-frame")).not.toHaveClass(/is-fullscreen/);
  await page.reload();
  await expect(page.getByRole("button", { name: "每周时段", exact: true })).toHaveClass("active");
  await page.getByRole("button", { name: "恢复上次查询", exact: true }).click();
  await expect(page.locator(".result-toolbar p")).toHaveText(summary);
  await expect(page.getByRole("button", { name: "具体日期", exact: true })).toHaveClass("active");
  await expect(page.getByRole("switch")).toBeChecked();
  // 中等桌面宽度将地点列表移到地图下方，仍不能产生页面横向滚动。
  await page.setViewportSize({ width: 1000, height: 900 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  expect(errors).toEqual([]);
});

test("手机布局无横向溢出且可切换地图与列表", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await calculate(page);
  await page.getByRole("button", { name: "地点与行程", exact: true }).click();
  await expect(page.locator(".city-card").first()).toBeVisible();
  await page.getByRole("button", { name: "地图", exact: true }).click();
  await expect(page.locator("#reachability-map")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

test("在线导出生成地图，离线 ZIP 在断网环境可独立打开", async ({ page, context }) => {
  await calculate(page);
  const folder = await mkdtemp(join(tmpdir(), "tarinmap-export-test-"));
  const errors: string[] = [];
  const exported = await context.newPage();
  exported.on("pageerror", error => errors.push(error.message));
  try {
    const onlineDownload = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出 HTML", exact: true }).click();
    const online = join(folder, "online.html");
    await (await onlineDownload).saveAs(online);
    await exported.goto(pathToFileURL(online).href);
    await expect(exported.locator(".leaflet-container")).toBeVisible();
    await expect(exported.locator(".leaflet-overlay-pane path").first()).toBeVisible();
    // 在线底图必须真实加载，不能仅凭 Leaflet 容器存在判定成功。
    await expect.poll(() => exported.locator("img.leaflet-tile").evaluateAll(
      images => images.filter(image => (image as HTMLImageElement).naturalWidth > 0).length,
    ), { timeout: 30000 }).toBeGreaterThan(0);
    const offlineDownload = page.waitForEvent("download");
    await page.getByRole("button", { name: "离线压缩包", exact: true }).click();
    const archive = join(folder, "offline.zip");
    await (await offlineDownload).saveAs(archive);
    execFileSync("python", ["-m", "zipfile", "-e", archive, folder]);
    await context.setOffline(true);
    const requests: string[] = [];
    exported.on("request", request => {
      if (/^https?:/.test(request.url())) requests.push(request.url());
    });
    await exported.goto(pathToFileURL(join(folder, "index.html")).href);
    await expect(exported.locator("#map")).toBeVisible();
    expect(await exported.locator("#map circle").count()).toBeGreaterThan(1);
    await exported.locator(".city button").first().click();
    await expect(exported.locator(".city .details").first()).toBeVisible();
    const map = exported.locator("#map");
    const before = await map.getAttribute("viewBox");
    await map.hover();
    await exported.mouse.wheel(0, -200);
    await expect(map).not.toHaveAttribute("viewBox", before!);
    expect(requests).toEqual([]);
    expect(errors).toEqual([]);
  } finally {
    await context.setOffline(false);
    await exported.close();
    await rm(folder, { recursive: true, force: true });
  }
});

test("刷新失败显示原因且保留可查询的旧快照", async ({ page }) => {
  // 故障分支使用接口替身，避免依赖真实远端故障或修改本地铁路数据。
  await page.route("**/api/data-status/refresh", route => route.fulfill({
    json: { state: "failed", message: "测试网络中断，旧快照已保留" },
  }));
  await page.goto("/");
  await expect(page.locator(".station-tags button").first()).toBeVisible();
  await page.getByRole("button", { name: "手动刷新", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("旧快照已保留");
  await expect(page.getByRole("button", { name: "计算铁路可达范围", exact: true })).toBeEnabled();
});
