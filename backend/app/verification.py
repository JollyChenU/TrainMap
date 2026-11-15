"""用户主动触发的低频 12306 经停核验，不参与全国批量计算。"""

import json
from datetime import datetime
from threading import Lock
from time import monotonic
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import LegResult

_lock = Lock()
_last_request = 0.0


def _json(url: str) -> dict:
    """读取固定官方入口，并限制响应大小。"""

    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://kyfw.12306.cn/otn/leftTicket/init",
    })
    with urlopen(request, timeout=20) as response:
        content = response.read(2 * 1024 * 1024 + 1)
    if len(content) > 2 * 1024 * 1024:
        raise ValueError("官方响应过大")
    return json.loads(content)


def verify_leg(leg: LegResult) -> dict:
    """只核验已有行程，按车次精确匹配，避免搜索前缀误命中。"""

    global _last_request
    with _lock:
        now = monotonic()
        if now - _last_request < 5:
            raise ValueError("请间隔至少 5 秒再核验，避免频繁访问 12306")
        _last_request = now
    if leg.service_date is None:
        raise ValueError("旧查询未保存列车始发日，请重新计算")
    query_time = datetime.now().astimezone().isoformat()
    search = _json("https://search.12306.cn/search/v1/train/search?" + urlencode({
        "keyword": leg.train_code, "date": leg.service_date.strftime("%Y%m%d"),
    }))
    matches = [row for row in search.get("data", [])
               if row.get("station_train_code") == leg.train_code]
    if len(matches) != 1:
        raise ValueError("官方接口未返回唯一匹配车次，不能据此认定列车停运")
    response = _json("https://kyfw.12306.cn/otn/czxx/queryByTrainNo?" + urlencode({
        "train_no": matches[0]["train_no"],
        "from_station_telecode": leg.from_station_id,
        "to_station_telecode": leg.to_station_id,
        "depart_date": leg.service_date.isoformat(),
    }))
    stops = response.get("data", {}).get("data", [])
    if not stops:
        raise ValueError("官方接口未返回经停信息，原快照不受影响")
    return {
        "train_code": leg.train_code,
        "queried_at": query_time,
        "service_date": leg.service_date.isoformat(),
        "note": "12306 当前返回的经停信息，仅供对照；未修改缓存结果，不代表余票或实时运行保证。",
        "stops": [{k: row.get(k, "") for k in
                   ("station_name", "arrive_time", "start_time", "arrive_day_diff")}
                  for row in stops],
    }
