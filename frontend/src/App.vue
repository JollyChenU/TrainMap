<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { api } from "./api/client";
import type {
  City,
  CityResult,
  DataStatus,
  QueryPayload,
  ReachabilityResponse,
  Station,
  LegResult,
  Verification,
  ExportBootstrap,
} from "./api/types";
import ReachabilityMap from "./components/ReachabilityMap.vue";

type TimeMode = "concrete" | "weekly";
type MobileView = "map" | "list";

const exportBootstrap: ExportBootstrap | null = window.__TARINMAP_EXPORT_DATA__ ?? null;
const exportMode = Boolean(exportBootstrap);

const cities = ref<City[]>([]);
const stations = ref<Station[]>([]);
const status = ref<DataStatus | null>(null);
const result = ref<ReachabilityResponse | null>(null);
const selectedCityId = ref<string | null>(null);
const selectedStationId = ref<string | null>(null);
const loading = ref(false);
const refreshing = ref(false);
const errorMessage = ref("");
const mobileView = ref<MobileView>("map");
const destinationSearch = ref("");
const controlsCollapsed = ref(false);
const destinationsCollapsed = ref(false);
const refreshMessage = ref("");
const verifying = ref(false);
const verification = ref<Verification | null>(null);
const lastQueryId = ref<string | null>(null);
const originSearch = ref("");
const originSearchFocused = ref(false);
const selectedOriginStationIds = ref<string[]>([]);
let disposed = false;
const DEFAULT_ORIGIN_CITY_NAME = "杭州";
const originCandidates = computed(() => {
  const keyword = originSearch.value.trim();
  if (!keyword) return [];
  return cities.value.filter(city => city.name.includes(keyword)).slice(0, 12);
});

const originCityId = ref("");
const timeMode = ref<TimeMode>("weekly");
const concreteStart = ref("");
const concreteEnd = ref("");
const weeklyStartDay = ref(5);
const weeklyStartTime = ref("20:00");
const weeklyEndDay = ref(6);
const weeklyEndTime = ref("12:00");
const transfersEnabled = ref(false);
const maxTransfers = ref(1);
const minTransferMinutes = ref(30);
const selectedTrainTypes = ref(["G", "D", "C", "K"]);

const trainTypeOptions = [
  { value: "G", label: "高铁 G" },
  { value: "D", label: "动车 D" },
  { value: "C", label: "城际 C" },
  { value: "K", label: "普速 K" },
  { value: "T", label: "特快 T" },
  { value: "Z", label: "直达 Z" },
  { value: "S", label: "市郊 S" },
  { value: "Y", label: "旅游 Y" },
  { value: "OTHER", label: "其他普速" },
];
const weekdayOptions = [
  { value: 1, label: "周一" },
  { value: 2, label: "周二" },
  { value: 3, label: "周三" },
  { value: 4, label: "周四" },
  { value: 5, label: "周五" },
  { value: 6, label: "周六" },
  { value: 7, label: "周日" },
];

const originCity = computed(
  () => cities.value.find((city) => city.city_id === originCityId.value) ?? null,
);
const selectedOriginStations = computed(() => {
  const selected = new Set(selectedOriginStationIds.value);
  return stations.value.filter(station => selected.has(station.station_id));
});
const selectedCity = computed(
  () =>
    result.value?.cities.find((city) => city.city_id === selectedCityId.value) ??
    null,
);
const filteredCities = computed(() => {
  const keyword = destinationSearch.value.trim();
  if (!result.value) return [];
  const matches = keyword ? result.value.cities.filter(
    (city) =>
      city.city_name.includes(keyword) ||
      city.stations.some((station) => station.station_name.includes(keyword)),
  ) : [...result.value.cities];
  return matches.sort((left, right) => {
    if (left.city_id === selectedCityId.value) return -1;
    if (right.city_id === selectedCityId.value) return 1;
    return 0;
  });
});

function orderedStations(city: CityResult) {
  if (!selectedStationId.value) return city.stations;
  return [...city.stations].sort((left, right) => {
    if (left.station_id === selectedStationId.value) return -1;
    if (right.station_id === selectedStationId.value) return 1;
    return 0;
  });
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function dateInputValue(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function initializeConcreteWindow(): void {
  const now = beijingNow();
  const day = now.getDay() || 7;
  const daysUntilFriday = (5 - day + 7) % 7;
  const friday = new Date(now);
  friday.setDate(now.getDate() + daysUntilFriday);
  friday.setHours(20, 0, 0, 0);
  if (friday <= now) friday.setDate(friday.getDate() + 7);
  const saturday = new Date(friday);
  saturday.setDate(friday.getDate() + 1);
  saturday.setHours(12, 0, 0, 0);
  concreteStart.value = dateInputValue(friday);
  concreteEnd.value = dateInputValue(saturday);
}

function nextWeekdayDate(weekday: number, timeValue: string): Date {
  const now = beijingNow();
  const currentDay = now.getDay() || 7;
  const days = (weekday - currentDay + 7) % 7;
  const [hours, minutes] = timeValue.split(":").map(Number);
  const target = new Date(now);
  target.setDate(now.getDate() + days);
  target.setHours(hours, minutes, 0, 0);
  if (target <= now) target.setDate(target.getDate() + 7);
  return target;
}

function beijingNow(): Date {
  // 将北京时间墙上时刻用于日期选择，不依赖浏览器所在时区。
  return new Date(new Date().toLocaleString("en-US", { timeZone: "Asia/Shanghai" }));
}

function normalizeTimeWindow(): { start: string; end: string } {
  if (timeMode.value === "concrete") {
    return {
      start: `${concreteStart.value}:00+08:00`,
      end: `${concreteEnd.value}:00+08:00`,
    };
  }

  const start = nextWeekdayDate(weeklyStartDay.value, weeklyStartTime.value);
  const startWeekday = start.getDay() || 7;
  let dayOffset = (weeklyEndDay.value - startWeekday + 7) % 7;
  const [endHours, endMinutes] = weeklyEndTime.value.split(":").map(Number);
  if (
    dayOffset === 0 &&
    (endHours < start.getHours() ||
      (endHours === start.getHours() && endMinutes <= start.getMinutes()))
  ) {
    dayOffset = 7;
  }
  const end = new Date(start);
  end.setDate(start.getDate() + dayOffset);
  end.setHours(endHours, endMinutes, 0, 0);
  return {
    start: `${dateInputValue(start)}:00+08:00`,
    end: `${dateInputValue(end)}:00+08:00`,
  };
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function formatFullDateTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

async function loadOriginStations(): Promise<void> {
  stations.value = await api.stations(originCityId.value);
  selectedOriginStationIds.value = stations.value.map(station => station.station_id);
}

function toggleOriginStation(stationId: string): void {
  selectedOriginStationIds.value = selectedOriginStationIds.value.includes(stationId)
    ? selectedOriginStationIds.value.filter(id => id !== stationId)
    : [...selectedOriginStationIds.value, stationId];
}

function selectAllOriginStations(): void {
  selectedOriginStationIds.value = stations.value.map(station => station.station_id);
}

async function handleOriginChange(): Promise<void> {
  loading.value = true;
  result.value = null;
  selectedCityId.value = null;
  selectedStationId.value = null;
  stations.value = [];
  errorMessage.value = "";
  try {
    // 加载期间禁用条件，避免快速切换城市时异步车站列表相互覆盖。
    await loadOriginStations();
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "切换城市失败";
  } finally {
    loading.value = false;
  }
}

function closeOriginResults(): void {
  window.setTimeout(() => { originSearchFocused.value = false; }, 120);
}

async function selectOriginCity(city: City): Promise<void> {
  if (city.city_id === originCityId.value) {
    originSearch.value = "";
    originSearchFocused.value = false;
    return;
  }
  originCityId.value = city.city_id;
  originSearch.value = "";
  originSearchFocused.value = false;
  await handleOriginChange();
}

async function refreshData(): Promise<void> {
  if (refreshing.value) return;
  refreshing.value = true;
  errorMessage.value = "";
  try {
    let job = await api.refresh();
    while (job.state === "running" && !disposed) {
      refreshMessage.value = job.message;
      await new Promise(resolve => setTimeout(resolve, 1200));
      job = await api.refreshProgress();
    }
    if (disposed) return;
    if (job.state !== "completed") throw new Error(job.message || "数据任务已中断，请重试");
    const oldName = originCity.value?.name;
    [cities.value, status.value] = await Promise.all([api.cities(), api.dataStatus()]);
    originCityId.value = cities.value.find(city => city.name === oldName)?.city_id
      ?? cities.value.find(city => city.name === DEFAULT_ORIGIN_CITY_NAME)?.city_id
      ?? cities.value[0]?.city_id ?? "";
    await loadOriginStations();
    result.value = null;
    selectedCityId.value = null;
    selectedStationId.value = null;
    refreshMessage.value = "";
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "刷新失败";
  } finally {
    refreshing.value = false;
  }
}

async function calculate(): Promise<void> {
  errorMessage.value = "";
  if (!selectedOriginStationIds.value.length) {
    errorMessage.value = "请至少选择一个始发站";
    return;
  }
  if (!selectedTrainTypes.value.length) {
    errorMessage.value = "请至少选择一种列车类型";
    return;
  }
  if (!weeklyStartTime.value || !weeklyEndTime.value ||
      (timeMode.value === "concrete" && (!concreteStart.value || !concreteEnd.value))) {
    errorMessage.value = "请填写完整的出发和到达时间";
    return;
  }
  const window = normalizeTimeWindow();
  const payload: QueryPayload = {
    origin_city_id: originCityId.value,
    origin_station_ids: [...selectedOriginStationIds.value],
    start_at: window.start,
    end_at: window.end,
    train_types: selectedTrainTypes.value,
    transfers_enabled: transfersEnabled.value,
    max_transfers: transfersEnabled.value ? maxTransfers.value : 0,
    min_transfer_minutes: minTransferMinutes.value,
  };

  loading.value = true;
  try {
    result.value = await api.reachability(payload);
    lastQueryId.value = result.value.query_id;
    try { localStorage.setItem("tarinmap-last-query", result.value.query_id); } catch { /* 隐私模式允许不保存。 */ }
    status.value = result.value.data_status;
    selectedCityId.value = result.value.cities[0]?.city_id ?? null;
    selectedStationId.value = null;
    mobileView.value = "map";
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "计算失败";
  } finally {
    loading.value = false;
  }
}

function selectLocation(cityId: string, stationId?: string): void {
  selectedCityId.value = cityId;
  selectedStationId.value = stationId ?? null;
  if (window.matchMedia("(max-width: 780px)").matches) mobileView.value = "list";
}

function exportResult(mode: "online" | "offline"): void {
  if (!result.value) return;
  window.location.href = `/api/results/${encodeURIComponent(result.value.query_id)}/export?mode=${mode}`;
}

function showExportVariant(includeTransfers: boolean): void {
  if (!exportBootstrap?.direct_result) return;
  const nextResult = includeTransfers
    ? exportBootstrap.result
    : exportBootstrap.direct_result;
  result.value = nextResult;
  status.value = nextResult.data_status;
  maxTransfers.value = nextResult.request.max_transfers;
  if (!nextResult.cities.some(city => city.city_id === selectedCityId.value)) {
    selectedCityId.value = nextResult.cities[0]?.city_id ?? null;
    selectedStationId.value = null;
  } else if (selectedStationId.value && !nextResult.cities.some(
    city => city.stations.some(station => station.station_id === selectedStationId.value),
  )) {
    selectedStationId.value = null;
  }
}

async function verify(leg: LegResult): Promise<void> {
  if (!result.value || !leg.service_date) return;
  verifying.value = true;
  errorMessage.value = "";
  try {
    verification.value = await api.verify(result.value.query_id, leg.train_code, leg.service_date);
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "核验失败";
  } finally {
    verifying.value = false;
  }
}

async function restoreQuery(): Promise<void> {
  if (!lastQueryId.value) return;
  loading.value = true;
  errorMessage.value = "";
  try {
    const saved = await api.result(lastQueryId.value);
    if (!cities.value.some(city => city.city_id === saved.origin_city_id)) {
      throw new Error("历史查询的始发城市已不在当前目录，请重新查询");
    }
    originCityId.value = saved.origin_city_id;
    await loadOriginStations();
    selectedOriginStationIds.value = saved.request.origin_station_ids?.length
      ? saved.request.origin_station_ids.filter(id => stations.value.some(station => station.station_id === id))
      : stations.value.map(station => station.station_id);
    timeMode.value = "concrete";
    concreteStart.value = saved.request.start_at.slice(0, 16);
    concreteEnd.value = saved.request.end_at.slice(0, 16);
    selectedTrainTypes.value = [...saved.request.train_types];
    transfersEnabled.value = saved.request.transfers_enabled;
    maxTransfers.value = saved.request.max_transfers;
    minTransferMinutes.value = saved.request.min_transfer_minutes;
    await nextTick();
    result.value = saved;
    selectedCityId.value = saved.cities[0]?.city_id ?? null;
    selectedStationId.value = null;
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "历史查询不存在";
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  initializeConcreteWindow();
  if (exportBootstrap) {
    const saved = exportBootstrap.result;
    status.value = saved.data_status;
    result.value = saved;
    stations.value = exportBootstrap.origin_stations;
    cities.value = [{
      city_id: saved.origin_city_id,
      name: saved.origin_city_name,
      longitude: saved.origin_longitude,
      latitude: saved.origin_latitude,
      station_count: exportBootstrap.origin_stations.length,
    }];
    originCityId.value = saved.origin_city_id;
    selectedOriginStationIds.value = saved.origin_station_ids.length
      ? [...saved.origin_station_ids]
      : exportBootstrap.origin_stations.map(station => station.station_id);
    timeMode.value = "concrete";
    concreteStart.value = saved.request.start_at.slice(0, 16);
    concreteEnd.value = saved.request.end_at.slice(0, 16);
    selectedTrainTypes.value = [...saved.request.train_types];
    transfersEnabled.value = saved.request.transfers_enabled;
    maxTransfers.value = saved.request.max_transfers;
    minTransferMinutes.value = saved.request.min_transfer_minutes;
    selectedCityId.value = saved.cities[0]?.city_id ?? null;
    return;
  }
  try {
    [cities.value, status.value] = await Promise.all([
      api.cities(),
      api.dataStatus(),
    ]);
    originCityId.value = cities.value.find(city => city.name === DEFAULT_ORIGIN_CITY_NAME)?.city_id
      ?? cities.value[0]?.city_id ?? "";
    await loadOriginStations();
    try {
      lastQueryId.value = localStorage.getItem("tarinmap-last-query");
    } catch { /* 已清理的历史结果不影响新查询。 */ }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "初始化失败";
  }
});

// 条件变更后移除旧结果，避免将旧范围误当成当前条件计算结果。
watch([timeMode, concreteStart, concreteEnd, weeklyStartDay, weeklyStartTime,
  weeklyEndDay, weeklyEndTime, transfersEnabled, maxTransfers, minTransferMinutes,
  selectedTrainTypes, selectedOriginStationIds], () => {
    if (exportMode) return;
    result.value = null;
    selectedCityId.value = null;
    selectedStationId.value = null;
  }, { deep: true });
watch(transfersEnabled, value => {
  if (exportMode) showExportVariant(value);
});
onBeforeUnmount(() => { disposed = true; });
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div>
        <p class="eyebrow">按时刻计算铁路可达范围</p>
        <h1>铁路活动范围地图</h1>
      </div>
      <div class="status-chip" :class="{ demo: status?.source_kind === 'demo' }">
        <span class="status-dot"></span>
        {{ exportMode ? "本地结果" : status?.source_kind === "demo" ? "演示快照" : "铁路快照" }}
      </div>
    </header>

    <div v-if="status" class="data-banner">
      <div>
        <strong>{{ status.source_name }}</strong>
        <span>{{ status.note }}</span>
        <small v-if="status.source_kind === 'external'" style="display:block">
          {{ status.version }} · {{ status.station_count }} 站 / {{ status.trip_count }} 车次 ·
          有效期 {{ status.valid_from }} 至 {{ status.valid_until }}
          <a href="/api/data-status/quality" target="_blank" rel="noopener">查看校验清单</a>
          · 数据署名：wensimehrp / OpenStreetMap · CC BY 4.0
        </small>
      </div>
      <div class="data-actions">
        <time>更新于 {{ formatFullDateTime(status.fetched_at) }}</time>
        <button v-if="!exportMode" class="text-button" :disabled="refreshing || loading" @click="refreshData">
          {{ refreshing ? "刷新中…" : "手动刷新" }}
        </button>
      </div>
    </div>
    <div v-if="refreshing" class="data-banner" role="status">{{ refreshMessage || "正在准备铁路数据…" }}，请稍候；原有快照会保留至新数据校验成功。</div>
    <section v-if="verification" class="data-banner" aria-label="12306 经停核验">
      <div>
        <strong>{{ verification.train_code }} · 始发日 {{ verification.service_date }} · 核验于 {{ formatFullDateTime(verification.queried_at) }}</strong>
        <p>{{ verification.note }}</p>
        <div style="max-height:180px;overflow:auto">
          <div v-for="(stop, index) in verification.stops" :key="index">
            {{ stop.station_name }}：到达 {{ stop.arrive_time }} / 发车 {{ stop.start_time }}
          </div>
        </div>
      </div>
      <button class="text-button" @click="verification = null">关闭核验</button>
    </section>

    <div v-if="errorMessage" class="error-banner" role="alert">
      {{ errorMessage }}
      <button aria-label="关闭错误提示" @click="errorMessage = ''">×</button>
    </div>

    <main class="workspace" :class="{ 'controls-collapsed': controlsCollapsed }">
      <aside class="control-panel" :class="{ collapsed: controlsCollapsed }">
        <button
          class="panel-toggle control-toggle"
          type="button"
          :title="controlsCollapsed ? '展开查询条件' : '收起查询条件'"
          :aria-label="controlsCollapsed ? '展开查询条件' : '收起查询条件'"
          @click="controlsCollapsed = !controlsCollapsed"
        >
          {{ controlsCollapsed ? "›" : "‹" }}
          <span v-if="controlsCollapsed">查询条件</span>
        </button>
        <fieldset v-show="!controlsCollapsed" class="panel-content" :disabled="refreshing || loading" style="border:0;padding:0;margin:0;min-width:0">
        <section class="panel-section">
          <div class="section-heading">
            <span>01</span>
            <div><h2>选择始发城市</h2><p>城市内所有客运站均可作为起点</p></div>
          </div>
          <div class="origin-picker">
            <input
              v-model="originSearch"
              class="field"
              type="search"
              role="combobox"
              :disabled="exportMode"
              aria-label="搜索始发城市"
              aria-autocomplete="list"
              :aria-expanded="originSearchFocused && !!originSearch.trim()"
              :placeholder="`搜索始发城市（当前：${originCity?.name ?? '加载中'}）`"
              @focus="originSearchFocused = true"
              @blur="closeOriginResults"
            />
            <div v-if="originSearchFocused && originSearch.trim()" class="origin-results" role="listbox">
              <button
                v-for="city in originCandidates"
                :key="city.city_id"
                type="button"
                role="option"
                :aria-selected="city.city_id === originCityId"
                @mousedown.prevent="selectOriginCity(city)"
              >
                <strong>{{ city.name }}</strong>
                <span>{{ city.station_count }} 个客运站</span>
              </button>
              <p v-if="!originCandidates.length">没有匹配的城市</p>
            </div>
          </div>
          <div class="station-filter-heading">
            <span>始发站</span>
            <span>{{ selectedOriginStationIds.length }}/{{ stations.length }} 已选</span>
            <button type="button" :disabled="exportMode" @click="selectAllOriginStations">全选</button>
          </div>
          <div class="station-tags" role="group" aria-label="筛选始发站">
            <button
              v-for="station in stations"
              :key="station.station_id"
              type="button"
              :disabled="exportMode"
              :class="{ selected: selectedOriginStationIds.includes(station.station_id) }"
              :aria-pressed="selectedOriginStationIds.includes(station.station_id)"
              @click="toggleOriginStation(station.station_id)"
            >
              {{ station.name }}
            </button>
          </div>
          <small>仅从已选客运站出发；地图城市点取该城市的代表车站位置</small>
        </section>

        <section class="panel-section">
          <div class="section-heading">
            <span>02</span>
            <div><h2>设置时间范围</h2><p>按第一程发车与最后一程到达判断</p></div>
          </div>
          <small>北京时间；每周时段映射到下一次对应日期，使用缓存日历作为参考。</small>
          <div class="segmented">
            <button :disabled="exportMode" :class="{ active: timeMode === 'concrete' }" @click="timeMode = 'concrete'">具体日期</button>
            <button :disabled="exportMode" :class="{ active: timeMode === 'weekly' }" @click="timeMode = 'weekly'">每周时段</button>
          </div>
          <div v-if="timeMode === 'concrete'" class="time-fields">
            <label>最早出发<input v-model="concreteStart" class="field" type="datetime-local" :disabled="exportMode" /></label>
            <label>最晚到达<input v-model="concreteEnd" class="field" type="datetime-local" :disabled="exportMode" /></label>
          </div>
          <div v-else class="time-fields">
            <label>最早出发
              <div class="inline-fields">
                <select v-model="weeklyStartDay" class="field" :disabled="exportMode">
                  <option v-for="day in weekdayOptions" :key="day.value" :value="day.value">{{ day.label }}</option>
                </select>
                <input v-model="weeklyStartTime" class="field" type="time" :disabled="exportMode" />
              </div>
            </label>
            <label>最晚到达
              <div class="inline-fields">
                <select v-model="weeklyEndDay" class="field" :disabled="exportMode">
                  <option v-for="day in weekdayOptions" :key="day.value" :value="day.value">{{ day.label }}</option>
                </select>
                <input v-model="weeklyEndTime" class="field" type="time" :disabled="exportMode" />
              </div>
            </label>
          </div>
        </section>

        <section class="panel-section">
          <div class="section-heading">
            <span>03</span>
            <div><h2>列车与换乘</h2><p>车型筛选作用于每一段行程</p></div>
          </div>
          <div class="check-grid">
            <label v-for="type in trainTypeOptions" :key="type.value" class="check-pill">
              <input v-model="selectedTrainTypes" type="checkbox" :value="type.value" :disabled="exportMode" />
              <span>{{ type.label }}</span>
            </label>
          </div>
          <label class="switch-row">
            <span><strong>启用同站换乘</strong><small>不包含同城异站接驳</small></span>
            <input
              v-model="transfersEnabled"
              type="checkbox"
              role="switch"
              :disabled="exportMode && !exportBootstrap?.direct_result"
            />
          </label>
          <div v-if="transfersEnabled" class="transfer-fields">
            <label>最多换乘
              <select v-model="maxTransfers" class="field" :disabled="exportMode">
                <option :value="1">1 次</option>
                <option :value="2">2 次</option>
                <option :value="3">3 次</option>
              </select>
            </label>
            <label>最短间隔
              <div class="number-field"><input v-model.number="minTransferMinutes" type="number" min="0" max="720" :disabled="exportMode" /><span>分钟</span></div>
            </label>
          </div>
        </section>

        <button class="primary-button" :disabled="exportMode || loading || refreshing" @click="calculate">
          <span v-if="loading" class="spinner"></span>
          {{ loading ? "正在计算可达范围…" : "计算铁路可达范围" }}
        </button>
        </fieldset>
      </aside>

      <section class="result-area">
        <div class="result-toolbar">
          <div>
            <p v-if="result">
              <strong>{{ result.reachable_city_count }}</strong> 个地点分组
              <span>·</span>
              <strong>{{ result.reachable_station_count }}</strong> 个车站
            </p>
            <p v-else class="muted">设置条件后开始计算</p>
          </div>
          <div class="toolbar-actions">
            <button v-if="!exportMode && lastQueryId" :disabled="loading || refreshing" @click="restoreQuery">恢复上次查询</button>
            <button v-if="!exportMode" :disabled="!result" @click="exportResult('online')">导出 HTML</button>
            <button v-if="!exportMode" :disabled="!result" @click="exportResult('offline')">离线压缩包</button>
          </div>
        </div>
        <p v-if="result" class="muted">按城市归属聚合，归属待核验车站独立分组。结果使用 {{ result.data_status.version }}，计算于 {{ formatFullDateTime(result.generated_at) }}；{{ formatDateTime(result.request.start_at) }} 至 {{ formatDateTime(result.request.end_at) }}。</p>

        <div class="mobile-tabs">
          <button :class="{ active: mobileView === 'map' }" @click="mobileView = 'map'">地图</button>
          <button :class="{ active: mobileView === 'list' }" @click="mobileView = 'list'">地点与行程</button>
        </div>

        <div
          class="result-grid"
          :class="[
            `mobile-${mobileView}`,
            { 'destinations-collapsed': destinationsCollapsed },
          ]"
        >
          <div class="map-column">
            <ReachabilityMap
              :origin="originCity"
              :origin-stations="selectedOriginStations"
              :cities="result?.cities ?? []"
              :selected-city-id="selectedCityId"
              :selected-station-id="selectedStationId"
              :use-export-basemap="exportMode"
              @select="selectLocation"
            />
            <div v-if="!result" class="empty-map">
              <div class="compass">↗</div>
              <h2>从一座城市出发</h2>
              <p>在地图上查看指定时间窗口内，铁路能够抵达的城市和车站。</p>
            </div>
          </div>

          <aside class="destination-panel" :class="{ collapsed: destinationsCollapsed }">
            <button
              class="panel-toggle destination-toggle"
              type="button"
              :title="destinationsCollapsed ? '展开地点列表' : '收起地点列表'"
              :aria-label="destinationsCollapsed ? '展开地点列表' : '收起地点列表'"
              @click="destinationsCollapsed = !destinationsCollapsed"
            >
              {{ destinationsCollapsed ? "‹" : "›" }}
              <span v-if="destinationsCollapsed">地点列表</span>
            </button>
            <div v-show="!destinationsCollapsed" class="destination-content">
            <div class="destination-header">
              <div><h2>可达地点</h2><p>按最早到达时间排序</p></div>
              <input v-model="destinationSearch" type="search" placeholder="搜索城市或车站" />
            </div>
            <div v-if="!result" class="empty-list">尚未生成查询结果</div>
            <div v-else-if="!filteredCities.length" class="empty-list">没有匹配的地点</div>
            <div v-else class="city-list">
              <article
                v-for="city in filteredCities"
                :key="city.city_id"
                class="city-card"
                :class="{ selected: city.city_id === selectedCityId }"
              >
                <button class="city-summary" @click="selectLocation(city.city_id)">
                  <span class="route-badge" :class="{ transfer: !city.direct_available }">
                    {{ city.direct_available ? "直" : "换" }}
                  </span>
                  <span class="city-copy">
                    <strong>{{ city.city_name }}</strong>
                    <small>{{ city.stations.length }} 个可达车站</small>
                  </span>
                  <time>{{ formatDateTime(city.earliest_arrival_at) }}</time>
                </button>
                <div v-if="city.city_id === selectedCityId" class="itinerary">
                  <section
                    v-for="station in orderedStations(city)"
                    :key="station.station_id"
                    :class="{ 'selected-station': station.station_id === selectedStationId }"
                  >
                    <div class="station-title">
                      <strong>{{ station.station_name }}</strong>
                      <span>{{ station.transfers }} 次换乘</span>
                    </div>
                    <div v-for="(leg, index) in station.legs" :key="`${leg.train_code}-${index}`" class="leg-row">
                      <div class="train-code">{{ leg.train_code }}</div>
                      <div>
                        <p>{{ leg.from_station_name }} → {{ leg.to_station_name }}</p>
                        <small>{{ formatDateTime(leg.departure_at) }} — {{ formatDateTime(leg.arrival_at) }}</small>
                        <button v-if="!exportMode && leg.service_date && result.data_status.source_kind === 'external'"
                          class="text-button" :disabled="verifying" @click="verify(leg)">
                          {{ verifying ? "核验中…" : "12306 经停核验" }}
                        </button>
                      </div>
                    </div>
                  </section>
                </div>
              </article>
            </div>
            </div>
          </aside>
        </div>
      </section>
    </main>
  </div>
</template>
