"""生成可独立查看的在线 HTML 与完全离线 ZIP。"""

from __future__ import annotations

import html
import io
import json
import zipfile
from pathlib import Path

from .models import ReachabilityResponse


VIEWER_CSS = """
:root{color-scheme:light;font-family:"Microsoft YaHei",system-ui,sans-serif;color:#17202a}
*{box-sizing:border-box}body{margin:0;background:#f4f7f9}.shell{max-width:1280px;margin:auto;padding:20px}
.hero{background:#133c55;color:white;border-radius:16px;padding:22px}.hero h1{margin:0 0 8px}
.meta{opacity:.86;font-size:14px}.notice{margin:14px 0;padding:12px 14px;background:#fff4d6;border-left:4px solid #e6a700}
.layout{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(300px,.8fr);gap:16px}
.map,.panel{background:white;border-radius:16px;box-shadow:0 8px 24px #1d354615;overflow:hidden}
.map > svg{width:100%;height:560px;background:linear-gradient(145deg,#e7f1f4,#d9e9df)}
.panel{padding:16px;max-height:560px;overflow:auto}.city{border:1px solid #dde5e8;border-radius:10px;margin-bottom:10px}
.city button{width:100%;border:0;background:white;padding:13px;text-align:left;font-weight:700;cursor:pointer}
.details{display:none;padding:0 13px 13px}.city.open .details{display:block}.leg{font-size:13px;padding:7px 0;border-top:1px dashed #d9e0e3}
.marker{cursor:pointer;stroke:white;stroke-width:2}.marker.direct{fill:#127475}.marker.transfer{fill:#e67e22}
.origin{fill:#9c2c3c;stroke:white;stroke-width:3}.label{font-size:11px;pointer-events:none;paint-order:stroke;stroke:white;stroke-width:3px}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin-top:10px;font-size:13px}.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px}
@media(max-width:760px){.shell{padding:10px}.layout{grid-template-columns:1fr}.map > svg{height:390px}.panel{max-height:none}}
"""

VIEWER_JS = """
const data=window.__RAIL_RANGE_DATA__;
const svg=document.querySelector("#map");
const box={minX:73,maxX:135,minY:18,maxY:54,w:900,h:560,p:38};
const merc=y=>Math.log(Math.tan(Math.PI/4+Math.max(-85,Math.min(85,y))*Math.PI/360))*180/Math.PI;
const minY=merc(box.minY),maxY=merc(box.maxY);
const scale=Math.min((box.w-box.p*2)/(box.maxX-box.minX),(box.h-box.p*2)/(maxY-minY));
const px=x=>box.w/2+(x-(box.minX+box.maxX)/2)*scale;
const py=y=>box.h/2-(merc(y)-(minY+maxY)/2)*scale;
function element(tag,attrs,text){
  const node=document.createElementNS("http://www.w3.org/2000/svg",tag);
  for(const [key,value] of Object.entries(attrs))node.setAttribute(key,value);
  if(text)node.textContent=text;
  return node;
}
for(const feature of window.__LAND__.features){
  const polygons=feature.geometry.type==="Polygon"?[feature.geometry.coordinates]:feature.geometry.coordinates;
  const d=polygons.map(p=>p.map(r=>r.map((xy,i)=>(i?"L":"M")+px(xy[0])+","+py(xy[1])).join(" ")+"Z").join(" ")).join(" ");
  svg.appendChild(element("path",{d,fill:"#f8faf4",stroke:"#a3bbb4","stroke-width":0.7,"fill-rule":"evenodd"}));
}
function circle(x,y,r,cls,name,id){
  const group=element("g",{"data-id":id});
  const dot=element("circle",{class:cls,cx:px(x),cy:py(y),r});
  dot.appendChild(element("title",{},name));
  group.appendChild(dot);
  group.appendChild(element("text",{class:"label",x:px(x)+8,y:py(y)-8},name));
  group.addEventListener("click",()=>{const card=document.getElementById("city-"+id);if(card){card.classList.add("open");card.scrollIntoView({block:"nearest"});}});
  svg.appendChild(group);
}
circle(data.origin.longitude,data.origin.latitude,8,"origin",data.origin.name,"origin");
for(const city of data.cities)circle(city.longitude,city.latitude,city.direct?5:4,city.direct?"marker direct":"marker transfer",city.name,city.id);
let view=[0,0,900,560];
svg.addEventListener("wheel",event=>{
  event.preventDefault();const factor=event.deltaY>0?1.15:0.87;
  if(view[2]*factor<30||view[2]*factor>1800)return;
  view=[view[0]+view[2]*(1-factor)/2,view[1]+view[3]*(1-factor)/2,view[2]*factor,view[3]*factor];
  svg.setAttribute("viewBox",view.join(" "));
},{passive:false});
let drag=null;
svg.style.touchAction="none";
svg.addEventListener("pointerdown",e=>{drag=[e.clientX,e.clientY,...view];svg.setPointerCapture(e.pointerId)});
svg.addEventListener("pointermove",e=>{if(!drag)return;const r=svg.getBoundingClientRect();view=[drag[2]-(e.clientX-drag[0])*drag[4]/r.width,drag[3]-(e.clientY-drag[1])*drag[5]/r.height,drag[4],drag[5]];svg.setAttribute("viewBox",view.join(" "))});
svg.addEventListener("pointerup",()=>drag=null);
document.querySelectorAll(".city button").forEach(button=>button.addEventListener("click",()=>button.parentElement.classList.toggle("open")));
"""

ONLINE_JS = """
const data=window.__RAIL_RANGE_DATA__;
document.querySelector("#map").remove();
const div=document.createElement("div");div.style.height="560px";
document.querySelector(".map").prepend(div);
const map=L.map(div).setView([35,105],4);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",{
  attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',maxZoom:18
}).addTo(map);
const points=[[data.origin.latitude,data.origin.longitude]];
function marker(latitude,longitude,name,color,id,radius){
  const text=document.createElement("span");text.textContent=name;
  L.circleMarker([latitude,longitude],{radius,color:"#fff",weight:2,fillColor:color,fillOpacity:1})
    .bindTooltip(text).on("click",()=>{const card=document.getElementById("city-"+id);if(card){card.classList.add("open");card.scrollIntoView({block:"nearest"})}}).addTo(map);
}
marker(...points[0],data.origin.name,"#9c2c3c","origin",9);
for(const city of data.cities){
  points.push([city.latitude,city.longitude]);
  marker(city.latitude,city.longitude,city.name,city.direct?"#127475":"#e67e22",city.id,7);
  for(const station of city.stations)marker(station.latitude,station.longitude,station.name,"#577b82",city.id,3);
}
if(points.length>1)map.fitBounds(points,{padding:[30,30]});
document.querySelectorAll(".city button").forEach(button=>button.addEventListener("click",()=>button.parentElement.classList.toggle("open")));
"""

def _viewer_payload(result: ReachabilityResponse) -> dict:
    """将完整结果整理为查看器使用的稳定结构。"""

    origin = {
        "id": result.origin_city_id,
        "name": result.origin_city_name,
        "longitude": result.origin_longitude,
        "latitude": result.origin_latitude,
    }
    return {
        "queryId": result.query_id,
        "origin": origin,
        "cities": [
            {
                "id": city.city_id,
                "name": city.city_name,
                "longitude": city.longitude,
                "latitude": city.latitude,
                "arrival": city.earliest_arrival_at.isoformat(),
                "direct": city.direct_available,
                "stations": [
                    {
                        "name": station.station_name,
                        "longitude": station.longitude,
                        "latitude": station.latitude,
                        "arrival": station.arrival_at.isoformat(),
                        "transfers": station.transfers,
                        "legs": [
                            {
                                "code": leg.train_code,
                                "from": leg.from_station_name,
                                "to": leg.to_station_name,
                                "departure": leg.departure_at.isoformat(),
                                "arrival": leg.arrival_at.isoformat(),
                            }
                            for leg in station.legs
                        ],
                    }
                    for station in city.stations
                ],
            }
            for city in result.cities
        ],
        "status": result.data_status.model_dump(mode="json"),
        "request": result.request.model_dump(mode="json"),
        "generatedAt": result.generated_at.isoformat(),
    }


def _render_city_cards(payload: dict) -> str:
    """生成不依赖 JavaScript 也能阅读的结果列表。"""

    cards: list[str] = []
    for city in payload["cities"]:
        station_sections: list[str] = []
        for station in city["stations"]:
            legs = "".join(
                (
                    f'<div class="leg"><strong>{html.escape(leg["code"])}</strong> '
                    f'{html.escape(leg["from"])} → {html.escape(leg["to"])}<br>'
                    f'{html.escape(leg["departure"])} 至 {html.escape(leg["arrival"])}</div>'
                )
                for leg in station["legs"]
            )
            station_sections.append(
                f"<p><strong>{html.escape(station['name'])}</strong> · "
                f"{station['transfers']} 次换乘 · {html.escape(station['arrival'])}</p>{legs}"
            )
        cards.append(
            f'<section class="city" id="city-{html.escape(city["id"], quote=True)}"><button type="button">'
            f"{html.escape(city['name'])} · {html.escape(city['arrival'])}</button>"
            f'<div class="details">{"".join(station_sections)}</div></section>'
        )
    return "".join(cards) or "<p>当前条件下没有可达地点。</p>"


def build_html(result: ReachabilityResponse, offline: bool) -> bytes:
    """生成结果固化的单页查看器。"""

    payload = _viewer_payload(result)
    serialized = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    mode_name = "完全离线导出" if offline else "在线分享导出"
    # 在线查看器内嵌 Leaflet，仅底图瓦片联网；离线查看器使用本地真实地理轮廓。
    vendor_css, vendor_js = "", ""
    if not offline:
        vendor = Path(__file__).resolve().parents[2] / "frontend" / "node_modules" / "leaflet" / "dist"
        vendor_css = (vendor / "leaflet.css").read_text(encoding="utf-8")
        vendor_js = (vendor / "leaflet.js").read_text(encoding="utf-8")
    land = Path(__file__).with_name("land.geojson").read_text(encoding="utf-8") if offline else "{}"
    script = VIEWER_JS if offline else ONLINE_JS
    document = f"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(result.origin_city_name)}铁路可达范围</title><style>{vendor_css}\n{VIEWER_CSS}</style></head>
<body><main class="shell">
<header class="hero"><h1>{html.escape(result.origin_city_name)}铁路可达范围</h1>
<div class="meta">{mode_name} · {result.reachable_city_count} 个地点分组 · {result.reachable_station_count} 个车站</div></header>
<div class="notice">本结果来自{html.escape(result.data_status.source_name)}，仅供参考，不代表实时开行、余票或临时停运状态。</div>
<p>按城市归属聚合，归属待核验车站独立分组。{html.escape(result.data_status.note)}<br>
快照 {html.escape(result.data_status.version)} · 获取于 {html.escape(result.data_status.fetched_at.isoformat())}<br>
查询窗口 {html.escape(result.request.start_at.isoformat())} 至 {html.escape(result.request.end_at.isoformat())}<br>
{html.escape(result.data_status.license)}<br>
{html.escape(result.data_status.source_url)}<br>
{"底图：Natural Earth 公共领域简化地理数据，仅作位置参考；支持滚轮缩放和拖动，不包含街道。" if offline else "在线底图需要联网加载。"}
</p>
<div class="layout"><section class="map"><svg id="map" viewBox="0 0 900 560" role="img" aria-label="可达城市示意地图"></svg>
<div class="legend"><span><i class="dot" style="background:#9c2c3c"></i>始发城市</span><span><i class="dot" style="background:#127475"></i>直达</span><span><i class="dot" style="background:#e67e22"></i>换乘</span></div></section>
<aside class="panel"><h2>可达地点</h2>{_render_city_cards(payload)}</aside></div>
</main><script>{vendor_js}</script><script>window.__LAND__={land};window.__RAIL_RANGE_DATA__={serialized};</script><script>{script}</script></body></html>"""
    return document.encode("utf-8")


def build_offline_zip(result: ReachabilityResponse) -> bytes:
    """把本地查看器、许可说明和清单打包为 ZIP。"""

    html_bytes = build_html(result, offline=True)
    manifest = {
        "format": "tarinmap-offline-export",
        "version": 1,
        "query_id": result.query_id,
        "generated_at": result.generated_at.isoformat(),
        "entry": "index.html",
        "network_required": False,
        "data_status": result.data_status.model_dump(mode="json"),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.html", html_bytes)
        archive.writestr("result.json", result.model_dump_json(indent=2).encode("utf-8"))
        archive.writestr(
            "export-manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        archive.writestr(
            "README.txt",
            (
                "铁路活动范围地图离线导出\n\n"
                "解压后使用 Edge 或 Chrome 打开 index.html。\n"
                "地图为 Natural Earth 公共领域简化地理轮廓，不需要联网，不代表真实铁路线路。\n"
                "底图来源：https://www.naturalearthdata.com/\n"
                f"铁路数据来源：{result.data_status.source_url}\n"
                f"铁路数据许可：{result.data_status.license}\n"
                "查询结果仅供参考，不代表实时开行、余票或临时停运状态。\n"
            ).encode("utf-8"),
        )
    return buffer.getvalue()
