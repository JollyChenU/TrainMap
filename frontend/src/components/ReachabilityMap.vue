<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import L from "leaflet";
import type { City, CityResult, Station } from "../api/types";

type MapDisplayMode = "city" | "station";
type LabelMode = "auto" | "always" | "hidden";
type PointLike = Pick<Station, "latitude" | "longitude">;

interface MarkerDescriptor {
  point: L.LatLngExpression;
  name: string;
  kind: "origin" | "destination";
  cityId?: string;
  stationId?: string;
  direct?: boolean;
  transfers?: number;
}

const props = defineProps<{
  origin: City | null;
  originStations: Station[];
  cities: CityResult[];
  selectedCityId: string | null;
  selectedStationId: string | null;
}>();

const emit = defineEmits<{
  select: [cityId: string, stationId?: string];
}>();

let map: L.Map | null = null;
let resultLayer: L.LayerGroup | null = null;
let resizeObserver: ResizeObserver | null = null;
const mapElement = ref<HTMLElement | null>(null);
const fullscreen = ref(false);
const displayMode = ref<MapDisplayMode>("station");
const labelMode = ref<LabelMode>("auto");
const labelDensityThreshold = ref(30);
const tileState = ref<"loading" | "ready" | "error">("loading");

const referenceOutline: L.LatLngExpression[] = [
  [47.8, 87.2], [49.8, 100.2], [47.5, 117.4], [43.4, 131.0],
  [34.6, 122.7], [23.2, 116.7], [20.2, 109.2], [22.6, 100.1],
  [28.2, 91.3], [35.5, 78.8], [42.6, 82.4],
];

function label(text: string): HTMLElement {
  const element = document.createElement("span");
  element.textContent = text;
  return element;
}

/** 选择真实车站中的中心代表点，避免坐标均值落到不存在车站的位置。 */
function representativeStation<T extends PointLike>(stations: T[]): T | null {
  if (!stations.length) return null;
  let best = stations[0];
  let bestScore = Number.POSITIVE_INFINITY;
  for (const candidate of stations) {
    let score = 0;
    for (const other of stations) {
      const lon = candidate.longitude - other.longitude;
      const lat = candidate.latitude - other.latitude;
      score += lon * lon + lat * lat;
    }
    if (score < bestScore) {
      best = candidate;
      bestScore = score;
    }
  }
  return best;
}

function markerDescriptors(): MarkerDescriptor[] {
  const descriptors: MarkerDescriptor[] = [];
  if (displayMode.value === "city") {
    const originStation = representativeStation(props.originStations);
    if (props.origin && originStation) {
      descriptors.push({
        point: [originStation.latitude, originStation.longitude],
        name: `${props.origin.name} · 始发城市`,
        kind: "origin",
      });
    }
    for (const city of props.cities) {
      const station = representativeStation(city.stations);
      if (!station) continue;
      descriptors.push({
        point: [station.latitude, station.longitude],
        name: city.city_name,
        kind: "destination",
        cityId: city.city_id,
        direct: city.direct_available,
      });
    }
    return descriptors;
  }

  for (const station of props.originStations) {
    descriptors.push({
      point: [station.latitude, station.longitude],
      name: `${station.name} · 始发站`,
      kind: "origin",
      stationId: station.station_id,
    });
  }
  for (const city of props.cities) {
    for (const station of city.stations) {
      descriptors.push({
        point: [station.latitude, station.longitude],
        name: station.station_name,
        kind: "destination",
        cityId: city.city_id,
        stationId: station.station_id,
        direct: station.direct_available,
        transfers: station.transfers,
      });
    }
  }
  return descriptors;
}

function drawResults(fit = true): void {
  if (!map || !resultLayer) return;
  // Leaflet 的永久 Tooltip 需要先显式解绑，否则快速重绘时旧标签可能残留在 pane 中。
  resultLayer.eachLayer(layer => {
    if (layer instanceof L.CircleMarker) layer.unbindTooltip();
  });
  resultLayer.clearLayers();
  const descriptors = markerDescriptors();
  const points = descriptors.map(item => item.point);
  const bounds = map.getBounds();
  const visibleCount = descriptors.filter(item => bounds.contains(L.latLng(item.point))).length;
  const permanentNames = labelMode.value === "always"
    || (labelMode.value === "auto" && visibleCount <= labelDensityThreshold.value);

  for (const item of descriptors) {
    const isOrigin = item.kind === "origin";
    const selected = item.stationId
      ? item.stationId === props.selectedStationId
      : item.cityId === props.selectedCityId;
    const marker = L.circleMarker(item.point, {
      radius: selected ? 10 : isOrigin ? 8 : displayMode.value === "city" ? 8 : 5,
      color: selected ? "#132f3a" : "#ffffff",
      weight: selected ? 4 : isOrigin ? 3 : 2,
      fillColor: isOrigin ? "#a63c4a" : item.direct ? "#117a73" : "#e67e22",
      fillOpacity: 0.96,
    });
    const detail = isOrigin
      ? item.name
      : displayMode.value === "city"
        ? `${item.name} · ${item.direct ? "可直达" : "需换乘"}`
        : `${item.name} · ${item.transfers ?? 0} 次换乘`;
    marker.bindTooltip(label(detail), {
      direction: "top",
      offset: [0, -6],
      permanent: permanentNames,
      opacity: 0.94,
      className: permanentNames ? "density-label" : "",
    });
    if (item.cityId) {
      marker.on("click", () => emit("select", item.cityId as string, item.stationId));
    }
    marker.addTo(resultLayer);
  }

  if (!fit) return;
  if (points.length > 1) {
    map.fitBounds(L.latLngBounds(points), { padding: [36, 36], maxZoom: 7, animate: false });
  } else if (points.length === 1) {
    map.setView(points[0], 7);
  } else if (props.origin) {
    map.setView([props.origin.latitude, props.origin.longitude], 5);
  }
}

function toggleFullscreen(): void {
  fullscreen.value = !fullscreen.value;
  window.setTimeout(() => map?.invalidateSize({ animate: false }), 80);
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape" && fullscreen.value) {
    fullscreen.value = false;
    window.setTimeout(() => map?.invalidateSize({ animate: false }), 80);
  }
}

onMounted(() => {
  const initialCenter: L.LatLngExpression = props.origin
    ? [props.origin.latitude, props.origin.longitude]
    : [35.2, 104.2];
  map = L.map(mapElement.value as HTMLElement, {
    center: initialCenter,
    zoom: props.origin ? 5 : 4,
    minZoom: 3,
    maxZoom: 18,
    zoomDelta: 0.5,
    zoomSnap: 0.5,
    wheelPxPerZoomLevel: 120,
    wheelDebounceTime: 80,
    zoomControl: true,
    attributionControl: true,
  });
  map.getContainer().style.background = "#e9ece8";

  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    minZoom: 3,
    maxZoom: 18,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  })
    .on("load", () => { tileState.value = "ready"; })
    .on("tileerror", () => { if (tileState.value !== "ready") tileState.value = "error"; })
    .addTo(map);

  L.polygon(referenceOutline, {
    color: "#9db8b2", weight: 1.5, fillColor: "#f8fbf8", fillOpacity: 0.12, interactive: false,
  }).addTo(map);

  resultLayer = L.layerGroup().addTo(map);
  L.control.scale({ imperial: false, maxWidth: 120 }).addTo(map);
  map.on("zoomend moveend", () => drawResults(false));
  drawResults();

  resizeObserver = new ResizeObserver(() => map?.invalidateSize({ animate: false }));
  resizeObserver.observe(mapElement.value as HTMLElement);
  document.addEventListener("keydown", handleKeydown);
});

watch(
  () => [props.origin, props.originStations, props.cities],
  () => drawResults(),
  { deep: true },
);
watch(() => [props.selectedCityId, props.selectedStationId], () => drawResults(false));
watch(displayMode, () => drawResults(false));
watch(labelMode, () => drawResults(false));
watch(labelDensityThreshold, () => drawResults(false));

onBeforeUnmount(() => {
  document.removeEventListener("keydown", handleKeydown);
  resizeObserver?.disconnect();
  resizeObserver = null;
  map?.remove();
  map = null;
  resultLayer = null;
});
</script>

<template>
  <div class="map-frame" :class="{ 'is-fullscreen': fullscreen }">
    <div ref="mapElement" id="reachability-map" aria-label="铁路可达地点地图"></div>
    <button
      class="fullscreen-button"
      type="button"
      :title="fullscreen ? '退出全屏' : '地图全屏'"
      :aria-label="fullscreen ? '退出地图全屏' : '打开地图全屏'"
      @click="toggleFullscreen"
    >{{ fullscreen ? "↙" : "⛶" }}</button>
    <div class="map-display-controls" aria-label="地图显示设置">
      <div class="map-control-row">
        <span>标记</span>
        <div class="mode-switch compact-switch">
          <button :class="{ active: displayMode === 'city' }" @click="displayMode = 'city'">城市</button>
          <button :class="{ active: displayMode === 'station' }" @click="displayMode = 'station'">车站</button>
        </div>
      </div>
      <div class="map-control-row label-row">
        <span>名称</span>
        <div class="mode-switch label-switch" role="group" aria-label="名称显示方式">
          <button :class="{ active: labelMode === 'auto' }" @click="labelMode = 'auto'">自动</button>
          <button :class="{ active: labelMode === 'always' }" @click="labelMode = 'always'">始终</button>
          <button :class="{ active: labelMode === 'hidden' }" @click="labelMode = 'hidden'">隐藏</button>
        </div>
      </div>
      <label v-if="labelMode === 'auto'" class="density-control">
        <span>显示阈值</span>
        <input v-model.number="labelDensityThreshold" type="range" min="5" max="80" step="5" />
        <output>{{ labelDensityThreshold }}</output>
      </label>
    </div>
    <div class="map-legend" aria-label="地图图例">
      <span><i class="origin-dot"></i>始发</span>
      <span><i class="direct-dot"></i>直达</span>
      <span><i class="transfer-dot"></i>换乘</span>
    </div>
    <p v-if="tileState === 'loading'" class="tile-status">正在加载地图底图…</p>
    <p v-else-if="tileState === 'error'" class="tile-status error">在线底图加载失败，当前显示简化地理轮廓</p>
    <p class="map-caption">滚轮以半级平滑缩放</p>
  </div>
</template>

<style scoped>
.map-frame { position: relative; min-height: 100%; }
#reachability-map { min-height: 600px; height: calc(100vh - 170px); border-radius: 22px; overflow: hidden; }
.map-frame.is-fullscreen { position: fixed; z-index: 2000; inset: 0; width: 100vw; height: 100vh; padding: 0; background: #e9ece8; }
.map-frame.is-fullscreen #reachability-map { width: 100%; height: 100%; min-height: 100%; border-radius: 0; }
.fullscreen-button { position: absolute; z-index: 520; top: 84px; left: 12px; display: grid; width: 36px; height: 36px; border: 1px solid rgb(31 27 22 / 10%); border-radius: 8px; place-items: center; background: #fdfbf7; box-shadow: 0 1px 2px rgb(31 27 22 / 10%); color: #5e554a; font-size: 18px; font-weight: 500; cursor: pointer; }
.fullscreen-button:hover { background: #f4f8f8; }
.map-display-controls { position: absolute; z-index: 520; top: 12px; left: 52px; display: grid; gap: 6px; min-width: 260px; padding: 8px; border: 1px solid rgb(25 37 35 / 10%); border-radius: 12px; background: rgb(253 251 247 / 96%); box-shadow: 0 8px 24px rgb(31 27 22 / 8%); color: #5e554a; }
.map-control-row { display: grid; grid-template-columns: 36px 1fr; gap: 8px; align-items: center; }
.map-control-row > span, .density-control > span { font-size: 11px; color: #7b7266; }
.mode-switch { display: grid; padding: 2px; border-radius: 8px; background: #efe9df; }
.compact-switch { grid-template-columns: 1fr 1fr; }
.label-switch { grid-template-columns: repeat(3, 1fr); }
.map-display-controls button { min-height: 28px; padding: 4px 8px; border: 0; border-radius: 6px; background: transparent; color: #5e554a; font-size: 11px; font-weight: 500; cursor: pointer; transition: background-color 150ms cubic-bezier(0.22, 1, 0.36, 1), color 150ms cubic-bezier(0.22, 1, 0.36, 1); }
.map-display-controls button.active { background: #fdfbf7; color: #1f1b16; box-shadow: 0 1px 2px rgb(31 27 22 / 8%); }
.density-control { display: grid; grid-template-columns: 56px 1fr 24px; gap: 8px; align-items: center; padding-top: 2px; }
.density-control input { width: 100%; accent-color: #176a70; }
.density-control output { color: #1f1b16; font: 500 11px/1 ui-monospace, SFMono-Regular, Consolas, monospace; text-align: right; }
.map-legend { position: absolute; z-index: 500; left: 14px; bottom: 40px; display: flex; gap: 12px; padding: 9px 12px; border: 1px solid rgb(255 255 255 / 60%); border-radius: 999px; background: rgb(255 255 255 / 92%); box-shadow: 0 6px 20px rgb(16 63 79 / 14%); color: #24434c; font-size: 12px; backdrop-filter: blur(8px); }
.map-legend i { display: inline-block; width: 9px; height: 9px; margin-right: 5px; border-radius: 50%; }
.origin-dot { background: #a63c4a; }
.direct-dot { background: #117a73; }
.transfer-dot { background: #e67e22; }
.map-caption { position: absolute; z-index: 500; right: 12px; top: 12px; margin: 0; padding: 5px 8px; border: 1px solid rgb(25 37 35 / 8%); border-radius: 6px; background: rgb(253 251 247 / 92%); color: #6b665d; font-size: 11px; }
.tile-status { position: absolute; z-index: 510; top: 50%; left: 50%; margin: 0; padding: 9px 12px; border-radius: 9px; background: rgb(255 255 255 / 92%); box-shadow: 0 5px 18px rgb(24 58 68 / 15%); color: #3f626b; font-size: 12px; transform: translate(-50%, -50%); }
.tile-status.error { color: #8b541e; }
@media (max-width: 780px) {
  #reachability-map { height: 48vh; min-height: 370px; border-radius: 16px; }
  .map-caption { display: none; }
  .map-display-controls { right: 8px; left: 48px; min-width: 0; }
}
</style>
