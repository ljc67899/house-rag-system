# -*- coding: utf-8 -*-
"""外部地理数据（高德 Web 服务）

为什么需要它：
    房源库只覆盖北京。用户问「深圳宝安」时，我们不能编造房源，但也不该只会拒绝——
    该城市有哪些区、哪些商圈、哪些地铁站，是高德能给的**可核验事实**，
    把这些作为上下文喂给模型，回答才有依据，地图也有真实坐标。

三条硬约束：
    1. 只取地理信息（区名 / POI 名 / 经纬度 / 类型），不抓第三方房源页，不碰价格；
    2. 失败即降级：超时、额度、网络异常一律返回空列表，绝不拖垮问答主流程；
    3. 结果做 TTL 缓存：同城同区一天内不重复请求，既省额度也降延迟。

高德接口：
    行政区划   /v3/config/district
    关键字搜索 /v3/place/text
"""

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

import requests
from loguru import logger

from app.config import settings

_AMAP_BASE = "https://restapi.amap.com/v3"

# key -> (过期时间戳, 结果)
_CACHE: Dict[tuple, tuple] = {}

# 高德关键字搜索会混进餐饮/酒店类 POI，与「住在哪个片区」无关，直接过滤
_NOISE_WORDS = ("酒庄", "餐厅", "咖啡", "酒店", "KTV", "火锅", "烧烤", "便利店", "超市")

# 详情页「周边配套」分类：key -> (中文名, 高德 POI 类型码)
# 类型码来自高德 POI 分类编码表，多个用 | 连接表示并集
_SURROUND_CATEGORIES = (
    ("traffic", "交通", "150500|150700"),          # 地铁站 + 公交站
    ("school", "学校", "141200|141201|141202|141203|141204"),  # 各类学校
    ("food", "餐饮", "050000"),
    ("shop", "购物", "060000"),
    ("hospital", "医院", "090100"),
    ("bank", "银行", "160100"),
)


class ExternalGeoService:
    """高德 Web 服务封装（失败静默降级）"""

    def __init__(self) -> None:
        self.key = (settings.amap_web_key or "").strip()
        self.timeout = settings.external_data_timeout
        self.ttl = settings.external_data_cache_ttl

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    # ---------- 基础设施 ----------

    def _cache_get(self, key: tuple):
        hit = _CACHE.get(key)
        if not hit:
            return None
        expire_at, value = hit
        if expire_at < time.time():
            _CACHE.pop(key, None)
            return None
        return value

    def _cache_set(self, key: tuple, value) -> None:
        _CACHE[key] = (time.time() + self.ttl, value)

    def _request(self, path: str, params: dict) -> Optional[dict]:
        if not self.enabled:
            return None
        query = dict(params)
        query["key"] = self.key
        query["output"] = "JSON"
        try:
            resp = requests.get(f"{_AMAP_BASE}/{path}", params=query, timeout=self.timeout)
            data = resp.json()
        except Exception as exc:  # 网络 / 超时 / 非 JSON
            logger.warning(f"[外部地理] 高德请求失败: {exc}")
            return None
        # 高德成功返回 status == "1"
        if str(data.get("status")) != "1":
            logger.warning(
                f"[外部地理] 高德返回异常: info={data.get('info')} code={data.get('infocode')}"
            )
            return None
        return data

    # ---------- 业务方法 ----------

    def city_districts(self, city: str) -> List[str]:
        """城市下辖区县名（用于识别用户问的是哪个区）"""
        key = ("districts", city)
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        data = self._request(
            "config/district",
            {"keywords": city, "subdistrict": 1, "extensions": "base"},
        )
        names: List[str] = []
        for province in (data or {}).get("districts", []):
            # 传「深圳」时返回 city 层级，下挂各区；传「深圳市」时结构相同
            for sub in province.get("districts", []) or []:
                if sub.get("name"):
                    names.append(sub["name"])
        self._cache_set(key, names)
        return names

    def match_district(self, city: str, question: str) -> Optional[str]:
        """从问题里认出用户提到的区（如「宝安」→ 宝安区）"""
        q = question or ""
        for name in self.city_districts(city):
            short = name[:-1] if name.endswith(("区", "县", "市")) else name
            if name in q or (short and short in q):
                return name
        return None

    def business_areas(self, city: str, district: Optional[str] = None, limit: int = 8) -> List[dict]:
        """该城市（或指定区）的商圈 / 地铁站，带经纬度。

        返回字段对齐前端地图需要的形状：
            {name, district, type, lng, lat, source}
        """
        key = ("areas", city, district or "")
        cached = self._cache_get(key)
        if cached is not None:
            return cached

        pois: List[dict] = []
        seen = set()

        def _collect(keyword: str, types: str = "") -> None:
            params = {
                "keywords": keyword,
                "city": city,
                "citylimit": "true",
                "offset": limit,
                "page": 1,
                "extensions": "base",
            }
            if types:
                params["types"] = types
            data = self._request("place/text", params)
            for item in (data or {}).get("pois", []):
                location = (item.get("location") or "").split(",")
                if len(location) != 2:
                    continue
                name = (item.get("name") or "").strip()
                if not name or name in seen:
                    continue
                if any(word in name for word in _NOISE_WORDS):
                    continue
                try:
                    lng, lat = float(location[0]), float(location[1])
                except ValueError:
                    continue
                seen.add(name)
                pois.append(
                    {
                        "name": name,
                        "district": item.get("adname") or "",
                        "type": (item.get("type") or "").split(";")[-1] or "地点",
                        "lng": lng,
                        "lat": lat,
                        "source": "高德POI",
                    }
                )

        _collect(f"{district or city}商圈")
        if len(pois) < 4:
            _collect(f"{district or city}地铁站", types="150500")
        if len(pois) < 4:
            _collect(f"{district or city}写字楼", types="120201")

        result = pois[:limit]
        self._cache_set(key, result)
        logger.info(f"[外部地理] {city}{district or ''} 命中 {len(result)} 个地理点")
        return result

    def _around(self, lng: float, lat: float, types: str, radius: int, limit: int) -> Optional[List[dict]]:
        """单类周边搜索（place/around），按距离返回最近的若干 POI

        返回 None 表示这次请求失败（超时 / 限流 / 配额），调用方不能把它当成
        「该分类确实没有设施」，否则会把空结果缓存一天。
        """
        data = self._request(
            "place/around",
            {
                "location": f"{lng:.6f},{lat:.6f}",
                "types": types,
                "radius": radius,
                "offset": limit,
                "page": 1,
                "sortrule": "distance",
                "extensions": "base",
            },
        )
        if data is None:
            return None
        items: List[dict] = []
        for poi in (data or {}).get("pois", []):
            location = (poi.get("location") or "").split(",")
            if len(location) != 2:
                continue
            try:
                items.append(
                    {
                        "name": poi.get("name") or "",
                        "type": (poi.get("type") or "").split(";")[-1],
                        "address": poi.get("address") if isinstance(poi.get("address"), str) else "",
                        "distance": int(float(poi.get("distance") or 0)),
                        "lng": float(location[0]),
                        "lat": float(location[1]),
                    }
                )
            except ValueError:
                continue
        return items

    def surroundings(self, lng, lat, radius: int = 1500, limit: int = 6) -> List[dict]:
        """房源周边设施：按分类返回最近的 POI（含距离 / 地址 / 坐标）。

        高德 /place/around 一次只能查一组类型码，所以按分类各查一次——
        串行 6 次要 1~3 秒，详情页地图会明显「转圈」。改为只对缓存未命中的
        分类并发请求；但高德对并发很敏感（6 个同时打会返回
        CUQPS_HAS_EXCEEDED_THE_LIMIT，实测 3 并发稳定），因此限 3 并发 +
        被限流时退避重试一次。结果仍按「坐标 3 位 + 分类」缓存一天，失败不写缓存。
        """
        try:
            lng_f, lat_f = float(lng), float(lat)
        except (TypeError, ValueError):
            return []

        results: Dict[str, List[dict]] = {}
        pending = []
        for key, _label, types in _SURROUND_CATEGORIES:
            cache_key = ("around", round(lng_f, 3), round(lat_f, 3), key, radius)
            cached = self._cache_get(cache_key)
            if cached is None:
                pending.append((key, types, cache_key))
            else:
                results[key] = cached

        def _fetch(types: str) -> Optional[List[dict]]:
            items = self._around(lng_f, lat_f, types, radius, limit)
            if items is None:
                time.sleep(0.4)  # 让出配额，退避后重试一次
                items = self._around(lng_f, lat_f, types, radius, limit)
            return items

        if pending:
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = {
                    pool.submit(_fetch, types): (key, cache_key)
                    for key, types, cache_key in pending
                }
                for future, (key, cache_key) in futures.items():
                    try:
                        items = future.result()
                    except Exception as exc:  # 单类失败不影响其余分类
                        logger.warning(f"[外部地理] 周边分类 {key} 查询失败: {exc}")
                        items = None
                    if items is None:
                        # 失败不落缓存：下次进详情页还能重查，避免把空列表记一天
                        results[key] = []
                        continue
                    self._cache_set(cache_key, items)
                    results[key] = items

        return [
            {"key": key, "label": label, "items": results.get(key) or []}
            for key, label, _types in _SURROUND_CATEGORIES
        ]

    @staticmethod
    def format_distance(meters) -> str:
        try:
            m = int(meters)
        except (TypeError, ValueError):
            return "-"
        return f"{m} 米" if m < 1000 else f"{m / 1000:.1f} 公里"

    @staticmethod
    def format_for_prompt(areas: List[dict]) -> str:
        """把地理点拼成给模型的上下文（明确标注不含房价）"""
        if not areas:
            return ""
        lines = ["【外部地理数据】（来源：高德 POI，只含商圈/地铁等地理信息，不含任何房源价格）"]
        for a in areas:
            lines.append(f"- {a['name']}（{a['district'] or '未知行政区'}，{a['type']}）")
        return "\n".join(lines)


_service: Optional[ExternalGeoService] = None


def get_external_geo_service() -> ExternalGeoService:
    global _service
    if _service is None:
        _service = ExternalGeoService()
    return _service
