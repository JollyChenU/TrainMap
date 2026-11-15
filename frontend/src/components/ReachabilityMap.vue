<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import L from "leaflet";
import type { City, CityResult, Station } from "../api/types";

type MapDisplayMode = "city" | "station";
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

const LABEL_DENSITY_THRESHOLD = 28;
let map: L.Map | null = null;
let resultLayer: L.LayerGroup | null = null;
let resizeObserver: ResizeObserver | null = null;
const mapElement = ref<HTMLElement | null>(null);
const fullscreen = ref(false);
const displayMode = ref<MapDisplayMode>("station");
const showNames = ref(true);
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
  const permanentNames = showNames.value && visibleCount <= LABEL_DENSITY_THRESHOLD;

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
    zoomControl: true,
    attributionControl: true,
  });
  map.getContainer().style.background = "linear-gradient(145deg, #dcecef 0%, #e9f1e6 100%)";

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
watch(showNames, () => drawResults(false));

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
    <div class="map-display-controls" aria-label="地图标记设置">
      <div class="mode-switch">
        <button :class="{ active: displayMode === 'city' }" @click="displayMode = 'city'">城市</button>
        <button :class="{ active: displayMode === 'station' }" @click="displayMode = 'station'">车站</button>
      </div>
      <button class="name-toggle" :class="{ active: showNames }" :aria-pressed="showNames" @click="showNames = !showNames">
        {{ showNames ? "隐藏名称" : "显示名称" }}
      </button>
    </div>
    <div class="map-legend" aria-label="地图图例">
      <span><i class="origin-dot"></i>始发</span>
      <span><i class="direct-dot"></i>直达</span>
      <span><i class="transfer-dot"></i>换乘</span>
    </div>
    <p v-if="tileState === 'loading'" class="tile-status">正在加载地图底图…</p>
    <p v-else-if="tileState === 'error'" class="tile-status error">在线底图加载失败，当前显示简化地理轮廓</p>
    <p class="map-caption">名称仅在当前视野标点不超过 {{ LABEL_DENSITY_THRESHOLD }} 个时显示</p>
  </div>
</template>

<style scoped>
.map-frame { position: relative; min-height: 100%; }
#reachability-map { min-height: 600px; height: calc(100vh - 170px); border-radius: 22px; overflow: hidden; }
.map-frame.is-fullscreen { position: fixed; z-index: 2000; inset: 0; width: 100vw; height: 100vh; padding: 0; background: #dcecef; }
.map-frame.is-fullscreen #reachability-map { width: 100%; height: 100%; min-height: 100%; border-radius: 0; }
.fullscreen-button { position: absolute; z-index: 520; top: 82px; left: 10px; display: grid; width: 34px; height: 34px; border: 0; border-radius: 5px; place-items: center; background: white; box-shadow: 0 1px 5px rgb(0 0 0 / 28%); color: #315761; font-size: 19px; font-weight: 800; cursor: pointer; }
.fullscreen-button:hover { background: #f4f8f8; }
.map-display-controls { position: absolute; z-index: 520; top: 12px; left: 50px; display: flex; gap: 7px; padding: 5px; border: 1px solid rgb(255 255 255 / 70%); border-radius: 10px; background: rgb(255 255 255 / 94%); box-shadow: 0 5px 18px rgb(24 58 68 / 15%); }
.mode-switch { display: flex; padding: 2px; border-radius: 7px; background: #edf3f3; }
.map-display-controls button { padding: 6px 9px; border: 0; border-radius: 6px; background: transparent; color: #587078; font-size: 11px; font-weight: 800; cursor: pointer; }
.map-display-controls button.active { background: white; color: #176575; box-shadow: 0 1px 5px rgb(35 78 88 / 12%); }
.name-toggle { border-left: 1px solid #dce6e6 !important; border-radius: 0 6px 6px 0 !important; }
.map-legend { position: absolute; z-index: 500; left: 14px; bottom: 40px; display: flex; gap: 12px; padding: 9px 12px; border: 1px solid rgb(255 255 255 / 60%); border-radius: 999px; background: rgb(255 255 255 / 92%); box-shadow: 0 6px 20px rgb(16 63 79 / 14%); color: #24434c; font-size: 12px; backdrop-filter: blur(8px); }
.map-legend i { display: inline-block; width: 9px; height: 9px; margin-right: 5px; border-radius: 50%; }
.origin-dot { background: #a63c4a; }
.direct-dot { background: #117a73; }
.transfer-dot { background: #e67e22; }
.map-caption { position: absolute; z-index: 500; right: 12px; top: 12px; margin: 0; padding: 5px 8px; border-radius: 7px; background: rgb(255 255 255 / 88%); color: #526b72; font-size: 11px; }
.tile-status { position: absolute; z-index: 510; top: 50%; left: 50%; margin: 0; padding: 9px 12px; border-radius: 9px; background: rgb(255 255 255 / 92%); box-shadow: 0 5px 18px rgb(24 58 68 / 15%); color: #3f626b; font-size: 12px; transform: translate(-50%, -50%); }
.tile-status.error { color: #8b541e; }
@media (max-width: 780px) {
  #reachability-map { height: 48vh; min-height: 370px; border-radius: 16px; }
  .map-caption { top: 56px; max-width: 58%; text-align: right; }
  .map-display-controls { left: 48px; }
}
</style>
