import { test, expect, type Page } from "@playwright/test";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { execFileSync } from "node:child_process";
import type { QueryPayload } from "../src/api/types";

test("城市标签独立保存，地图全屏内切换比较范围和口径", async ({ page }) => {
  const dataStatus = await (await page.request.get("/api/data-status")).json();
  const response = await page.request.get("/api/cities");
  const cities = await response.json();
  const hangzhou = cities.find((city: { name: string }) => city.name === "杭州");
  const beijing = cities.find((city: { name: string }) => city.name === "北京");
  const requests: QueryPayload[][] = [];
  await page.route("**/api/reachability/compare", async route => {
    const queries: QueryPayload[] = route.request().postDataJSON();
    requests.push(queries);
    await route.fulfill({ json: queries.map(query => {
      const origin = query.origin_city_id === hangzhou.city_id ? hangzhou : beijing;
      const arrival = "2026-11-20T22:00:00+08:00";
      return {
        query_id: origin.city_id, generated_at: arrival, origin_city_name: origin.name,
        data_status: dataStatus, request: query,
        cities: [{
          city_id: "fixture", city_name: "共同目的地", latitude: 35, longitude: 115,
          earliest_arrival_at: arrival, direct_available: true,
          stations: [{
            station_id: origin.city_id + "-destination", station_name: origin.name + "方向站",
            latitude: 35, longitude: 115, arrival_at: arrival, direct_available: true, transfers: 0, legs: [],
          }],
        }],
      };
    }) });
  });

  await page.goto("/");
  const tabs = page.getByRole("navigation", { name: "始发城市配置" });
  const originInput = page.getByRole("combobox", { name: "搜索始发城市" });
  await expect(tabs.getByRole("button", { name: "杭州", exact: true })).toBeEnabled();
  await page.getByRole("switch").check();
  await page.getByRole("button", { name: "新增城市配置" }).click();
  await originInput.fill("北京");
  await page.getByRole("option", { name: /^北京\s/ }).first().click();
  await expect(page.getByRole("switch")).not.toBeChecked();
  await tabs.getByRole("button", { name: "杭州", exact: true }).click();
  await expect(page.getByRole("switch")).toBeChecked();
  await page.reload();
  await expect(tabs.getByRole("button", { name: "杭州", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("switch")).toBeChecked();

  const map = page.locator(".map-frame");
  const list = page.locator(".destination-panel");
  await map.getByRole("button", { name: "交集", exact: true }).click();
  await map.getByRole("button", { name: "计算比较" }).click();
  await expect(list.getByText("没有共同可达的地点")).toBeVisible();
  expect(requests[0].map(query => [query.origin_city_id, query.transfers_enabled])).toEqual([
    [hangzhou.city_id, true], [beijing.city_id, false],
  ]);
  await map.getByRole("button", { name: "城市", exact: true }).click();
  await expect(list.getByRole("button", { name: /共同目的地/ })).toContainText("2/2");
  await expect(map.locator(".origin-marker")).toHaveCount(2);
  await map.getByRole("button", { name: "打开地图全屏" }).click();
  await expect(map).toHaveClass(/is-fullscreen/);
  await map.getByRole("button", { name: "并集", exact: true }).click();
  await map.getByRole("button", { name: "车站", exact: true }).click();
  await expect(list.locator(".city-card")).toHaveCount(2);
  const origins = map.getByRole("group", { name: "参与比较的始发城市" });
  await origins.getByRole("button", { name: "北京", exact: true }).click();
  await expect(list.locator(".city-card")).toHaveCount(1);
  await expect(map.locator(".origin-marker")).toHaveCount(hangzhou.station_count);
  expect(requests).toHaveLength(1);
  await page.keyboard.press("Escape");
  await tabs.getByRole("button", { name: "北京", exact: true }).click();
  await tabs.getByRole("button", { name: "删除配置 北京" }).click();
  await expect(tabs.getByRole("button", { name: "北京", exact: true })).toHaveCount(0);
});

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
  await expect(page.getByRole("button", { name: "具体日期", exact: true })).toHaveClass("active");
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
  const queryResponse = await page.request.post("/api/reachability", {
    data: {
      origin_city_id: "beijing",
      start_at: "2026-11-20T20:00:00+08:00",
      end_at: "2026-11-21T12:00:00+08:00",
      train_types: ["G", "D", "K"],
      transfers_enabled: true,
      max_transfers: 1,
      min_transfer_minutes: 30,
    },
  });
  expect(queryResponse.ok()).toBe(true);
  const queryId = (await queryResponse.json()).query_id;
  const folder = await mkdtemp(join(tmpdir(), "tarinmap-export-test-"));
  const errors: string[] = [];
  let basemapResponses = 0;
  const exported = await context.newPage();
  exported.on("pageerror", error => errors.push(error.message));
  exported.on("response", response => {
    if (response.url().startsWith("https://tiles.openfreemap.org/") && response.ok()) {
      basemapResponses += 1;
    }
  });
  try {
    const onlineResponse = await page.request.get(`/api/results/${queryId}/export?mode=online`);
    expect(onlineResponse.ok()).toBe(true);
    const online = join(folder, "online.html");
    await writeFile(online, await onlineResponse.body());
    await exported.goto(pathToFileURL(online).href);
    await expect(exported.locator(".app-shell")).toBeVisible();
    await expect(exported.locator(".leaflet-container")).toBeVisible();
    await expect(exported.locator(".leaflet-overlay-pane path").first()).toBeVisible();
    await expect(exported.locator("canvas.maplibregl-canvas")).toBeVisible();
    const transferToggle = exported.getByRole("switch", { name: "启用同站换乘" });
    await expect(exported.getByRole("switch")).toHaveCount(1);
    await expect(transferToggle).toBeChecked();
    await expect(transferToggle).toBeEnabled();
    await transferToggle.uncheck();
    await expect(transferToggle).not.toBeChecked();
    // 在线矢量底图必须真实返回资源，不能仅凭地图容器存在判定成功。
    await expect.poll(() => basemapResponses, { timeout: 30000 }).toBeGreaterThan(0);
    await expect(exported.locator("body")).not.toContainText("Access blocked");
    const offlineResponse = await page.request.get(`/api/results/${queryId}/export?mode=offline`);
    expect(offlineResponse.ok()).toBe(true);
    const archive = join(folder, "offline.zip");
    await writeFile(archive, await offlineResponse.body());
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

test("初始化和切换城市不会自动刷新铁路数据", async ({ page }) => {
  let refreshRequests = 0;
  page.on("request", request => {
    if (request.method() === "POST" && request.url().endsWith("/api/data-status/refresh")) {
      refreshRequests += 1;
    }
  });

  await page.goto("/");
  await expect(page.locator(".station-tags button").first()).toBeVisible();
  await page.getByLabel("搜索始发城市", { exact: true }).fill("北京");
  await page.getByRole("option", { name: /北京/ }).click();
  await expect(page.locator(".station-tags button").first()).toBeVisible();
  expect(refreshRequests).toBe(0);
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
