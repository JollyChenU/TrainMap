<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import L from "leaflet";
import type { City, CityResult } from "../api/types";

const props = defineProps<{
  origin: City | null;
  cities: CityResult[];
  selectedCityId: string | null;
}>();

const emit = defineEmits<{
  select: [cityId: string];
}>();

let map: L.Map | null = null;
let resultLayer: L.LayerGroup | null = null;
let resizeObserver: ResizeObserver | null = null;
const mapElement = ref<HTMLElement | null>(null);
const mapFrame = ref<HTMLElement | null>(null);
const fullscreen = ref(false);
const tileState = ref<"loading" | "ready" | "error">("loading");

// 在线底图不可用时保留简化轮廓，避免地图区域完全空白。
const referenceOutline: L.LatLngExpression[] = [
  [47.8, 87.2],
  [49.8, 100.2],
  [47.5, 117.4],
  [43.4, 131.0],
  [34.6, 122.7],
  [23.2, 116.7],
  [20.2, 109.2],
  [22.6, 100.1],
  [28.2, 91.3],
  [35.5, 78.8],
  [42.6, 82.4],
];

function label(text: string): HTMLElement {
  // 外部快照中的名称只作为文本显示，不能作为 HTML 注入地图提示框。
  const element = document.createElement("span");
  element.textContent = text;
  return element;
}

function drawResults(fit = true): void {
  if (!map || !resultLayer) return;
  resultLayer.clearLayers();
  const points: L.LatLngExpression[] = [];

  if (props.origin) {
    const originPoint: L.LatLngExpression = [
      props.origin.latitude,
      props.origin.longitude,
    ];
    points.push(originPoint);
    L.circleMarker(originPoint, {
      radius: 9,
      color: "#ffffff",
      weight: 3,
      fillColor: "#a63c4a",
      fillOpacity: 1,
    })
      .bindTooltip(label(`${props.origin.name} · 始发城市`), {
        direction: "top",
        offset: [0, -7],
      })
      .addTo(resultLayer);
  }

  for (const city of props.cities) {
    const point: L.LatLngExpression = [city.latitude, city.longitude];
    points.push(point);
    const selected = city.city_id === props.selectedCityId;
    const marker = L.circleMarker(point, {
      radius: selected ? 10 : city.direct_available ? 8 : 7,
      color: selected ? "#132f3a" : "#ffffff",
      weight: selected ? 4 : 2,
      fillColor: city.direct_available ? "#117a73" : "#e67e22",
      fillOpacity: 0.96,
    });
    marker
      .bindTooltip(
        label(`${city.city_name} · ${city.direct_available ? "可直达" : "需换乘"}`),
        { direction: "top", offset: [0, -6] },
      )
      .on("click", () => emit("select", city.city_id))
      .addTo(resultLayer);
    if (map.getZoom() >= 8 || selected) {
      for (const station of city.stations) {
        L.circleMarker([station.latitude, station.longitude], {
          radius: 4, color: "#ffffff", weight: 1,
          fillColor: station.direct_available ? "#117a73" : "#e67e22", fillOpacity: 0.9,
        }).bindTooltip(label(`${station.station_name} · ${station.transfers} 次换乘`))
          .on("click", () => emit("select", city.city_id))
          .addTo(resultLayer);
      }
    }
  }

  if (!fit) return;
  if (points.length > 1) {
    map.fitBounds(L.latLngBounds(points), {
      padding: [36, 36],
      maxZoom: 6,
      animate: false,
    });
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
  map.getContainer().style.background =
    "linear-gradient(145deg, #dcecef 0%, #e9f1e6 100%)";

  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    minZoom: 3,
    maxZoom: 18,
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  })
    .on("load", () => {
      tileState.value = "ready";
    })
    .on("tileerror", () => {
      if (tileState.value !== "ready") tileState.value = "error";
    })
    .addTo(map);

  L.polygon(referenceOutline, {
    color: "#9db8b2",
    weight: 1.5,
    fillColor: "#f8fbf8",
    fillOpacity: 0.12,
    interactive: false,
  }).addTo(map);

  resultLayer = L.layerGroup().addTo(map);
  L.control.scale({ imperial: false, maxWidth: 120 }).addTo(map);
  map.on("zoomend", () => drawResults(false));
  drawResults();

  resizeObserver = new ResizeObserver(() => {
    map?.invalidateSize({ animate: false });
  });
  resizeObserver.observe(mapElement.value as HTMLElement);
  document.addEventListener("keydown", handleKeydown);
});

watch(
  () => [props.origin, props.cities],
  () => drawResults(),
  { deep: true },
);
watch(() => props.selectedCityId, () => drawResults(false));

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
  <div ref="mapFrame" class="map-frame" :class="{ 'is-fullscreen': fullscreen }">
    <div ref="mapElement" id="reachability-map" aria-label="铁路可达城市地图"></div>
    <button
      class="fullscreen-button"
      type="button"
      :title="fullscreen ? '退出全屏' : '地图全屏'"
      :aria-label="fullscreen ? '退出地图全屏' : '打开地图全屏'"
      @click="toggleFullscreen"
    >
      {{ fullscreen ? "↙" : "⛶" }}
    </button>
    <div class="map-legend" aria-label="地图图例">
      <span><i class="origin-dot"></i>始发</span>
      <span><i class="direct-dot"></i>直达</span>
      <span><i class="transfer-dot"></i>换乘</span>
    </div>
    <p v-if="tileState === 'loading'" class="tile-status">正在加载地图底图…</p>
    <p v-else-if="tileState === 'error'" class="tile-status error">
      在线底图加载失败，当前显示简化地理轮廓
    </p>
    <p class="map-caption">标记连线仅作行程示意，不代表真实铁路走向</p>
  </div>
</template>

<style scoped>
.map-frame {
  position: relative;
  min-height: 100%;
}

#reachability-map {
  min-height: 600px;
  height: calc(100vh - 170px);
  border-radius: 22px;
  overflow: hidden;
}

.map-frame.is-fullscreen {
  position: fixed;
  z-index: 2000;
  inset: 0;
  width: 100vw;
  height: 100vh;
  padding: 0;
  background: #dcecef;
}

.map-frame.is-fullscreen #reachability-map {
  width: 100%;
  height: 100%;
  min-height: 100%;
  border-radius: 0;
}

.fullscreen-button {
  position: absolute;
  z-index: 520;
  top: 82px;
  left: 10px;
  display: grid;
  width: 34px;
  height: 34px;
  border: 0;
  border-radius: 5px;
  place-items: center;
  background: white;
  box-shadow: 0 1px 5px rgb(0 0 0 / 28%);
  color: #315761;
  font-size: 19px;
  font-weight: 800;
  cursor: pointer;
}

.fullscreen-button:hover {
  background: #f4f8f8;
}

.map-legend {
  position: absolute;
  z-index: 500;
  left: 14px;
  bottom: 40px;
  display: flex;
  gap: 12px;
  padding: 9px 12px;
  border: 1px solid rgb(255 255 255 / 60%);
  border-radius: 999px;
  background: rgb(255 255 255 / 92%);
  box-shadow: 0 6px 20px rgb(16 63 79 / 14%);
  color: #24434c;
  font-size: 12px;
  backdrop-filter: blur(8px);
}

.map-legend i {
  display: inline-block;
  width: 9px;
  height: 9px;
  margin-right: 5px;
  border-radius: 50%;
}

.origin-dot {
  background: #a63c4a;
}

.direct-dot {
  background: #117a73;
}

.transfer-dot {
  background: #e67e22;
}

.map-caption {
  position: absolute;
  z-index: 500;
  right: 12px;
  top: 12px;
  margin: 0;
  padding: 5px 8px;
  border-radius: 7px;
  background: rgb(255 255 255 / 88%);
  color: #526b72;
  font-size: 11px;
}

.tile-status {
  position: absolute;
  z-index: 510;
  top: 50%;
  left: 50%;
  margin: 0;
  padding: 9px 12px;
  border-radius: 9px;
  background: rgb(255 255 255 / 92%);
  box-shadow: 0 5px 18px rgb(24 58 68 / 15%);
  color: #3f626b;
  font-size: 12px;
  transform: translate(-50%, -50%);
}

.tile-status.error {
  color: #8b541e;
}

@media (max-width: 780px) {
  #reachability-map {
    height: 48vh;
    min-height: 370px;
    border-radius: 16px;
  }

  .map-caption {
    max-width: 48%;
    text-align: right;
  }
}

</style>
