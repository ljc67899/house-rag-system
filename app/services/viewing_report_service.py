# -*- coding: utf-8 -*-
"""AI 看房报告 Agent（多模态：图片 + 周边 POI + 价格走势 + 模型估价）

为什么单独抽一个 Service：
    报告不是「再问一次模型」，而是先由四个工具各取一份**可核验的事实**，
    再由 LLM 做最后一步汇总。LLM 只负责组织语言与给出判断，数字全部来自系统：
        - 房源事实：house_info（挂牌价、面积、户型、朝向、楼层…）
        - 市场价格：MarketStatsService + EstimateService（同区域分位、模型估价区间）
        - 未来走势：TrendService（LSTM 未来 6 个月）
        - 周边配套：ExternalGeoService（高德 POI，地铁/学校/医院/购物）
        - 图片理解：可选的多模态模型（settings.llm_vision_model，未配置则跳过）

三条硬约束：
    1. 任何一个工具失败都只让对应小节降级，报告必须能出；
    2. LLM 不可用时用模板拼装，绝不返回空白；
    3. 结果做短 TTL 缓存，避免用户反复点「生成」把 token 烧光。
"""

import time
from typing import Optional

import requests
from loguru import logger
from sqlalchemy.orm import Session

from app.config import settings
from app.services.estimate_service import EstimateService
from app.services.external_geo_service import get_external_geo_service
from app.services.llm_service import get_llm_service
from app.services.market_stats import MarketStatsService
from app.services.trend_service import TrendService
from app.models.house import HouseInfo
from app.utils.price_utils import parse_area_to_sqm, parse_price_to_wan

REPORT_SYSTEM_PROMPT = """你是资深二手房分析师，正在为购房者撰写一份《看房报告》。
要求：
1. 只能使用给定数据，禁止编造小区、价格、距离、政策；数据缺失就写「暂无数据」
2. 输出 Markdown，严格使用这六个二级标题，顺序不变：
   ## 一句话结论
   ## 房源与价格诊断
   ## 周边与通勤
   ## 价格走势判断
   ## 议价与风险提示
   ## 建议下一步
3. 每个判断都要带数字（单价、差价、涨跌幅、距离），不要写空话
4. 全文 450~650 字，语气客观，不吹不黑
5. 「一句话结论」用一句话给出「值不值得看」的判断与理由
6. 「议价与风险提示」至少 2 条，要能落到具体动作（怎么砍价、要核实什么）"""

_CACHE: dict = {}
_CACHE_TTL = 600


class ViewingReportService:
    def __init__(self, db: Session):
        self.db = db

    # ---------- 事实采集 ----------

    def collect(self, house_id: int) -> Optional[dict]:
        """把报告需要的事实一次性取齐；每个工具独立降级"""
        house = self.db.query(HouseInfo).filter(HouseInfo.id == house_id).first()
        if not house:
            return None

        d = house.to_dict()
        price = parse_price_to_wan(d.get("price"))
        area = parse_area_to_sqm(d.get("area"))
        unit_price = round(price / area, 2) if (price and area) else None

        facts = {
            "house": d,
            "price": price,
            "area": area,
            "unit_price": unit_price,
            "estimate": None,
            "trend": None,
            "poi": [],
            "image_note": "",
        }

        # 1) 模型估价 + 区域行情
        try:
            facts["estimate"] = EstimateService(self.db).estimate_price(
                rooms=d.get("rooms") or "2室1厅",
                area=area or 80,
                region=d.get("region") or "",
                direction=d.get("direction") or "",
                decoration=d.get("decoration") or "",
                floor=d.get("floor") or "",
            )
        except Exception as e:
            logger.warning(f"[看房报告] 估价失败: {e}")

        try:
            stats = MarketStatsService(self.db).get_region_stats(d.get("region") or "")
            facts["market"] = MarketStatsService.public(stats)
        except Exception as e:
            logger.warning(f"[看房报告] 行情统计失败: {e}")
            facts["market"] = {}

        # 2) 未来 6 个月走势
        try:
            trend = TrendService(self.db).get_trend(d.get("region") or "", 6)
            if trend.get("status") == "ok":
                hist = trend.get("history") or []
                preds = trend.get("predictions") or []
                facts["trend"] = {
                    "latest_month": hist[-1]["month"] if hist else None,
                    "latest_price": hist[-1]["price"] if hist else None,
                    "mom_change": trend.get("mom_change"),
                    "predicted_change": trend.get("predicted_change"),
                    "method": trend.get("method"),
                    "predictions": preds[:6],
                }
        except Exception as e:
            logger.warning(f"[看房报告] 走势预测失败: {e}")

        # 3) 周边配套（高德 POI，未配置 key 时为空）
        try:
            geo = get_external_geo_service()
            if geo.enabled and d.get("longitude") and d.get("latitude"):
                for cat in geo.surroundings(d["longitude"], d["latitude"], 1500):
                    items = (cat.get("items") or [])[:2]
                    if items:
                        facts["poi"].append({
                            "label": cat.get("label"),
                            "items": [
                                {"name": i.get("name"), "distance": i.get("distance")}
                                for i in items
                            ],
                        })
        except Exception as e:
            logger.warning(f"[看房报告] 周边 POI 失败: {e}")

        # 4) 图片理解（可选，未配置视觉模型则跳过）
        try:
            facts["image_note"] = self._describe_image(d.get("image_url"))
        except Exception as e:
            logger.warning(f"[看房报告] 图片理解失败: {e}")

        return facts

    # ---------- 多模态：图片理解 ----------

    @staticmethod
    def _describe_image(image_url: Optional[str]) -> str:
        """调用多模态模型描述房源图片；未配置 LLM_VISION_MODEL 或图片不可达时返回空串"""
        model = (getattr(settings, "llm_vision_model", "") or "").strip()
        if not model or not image_url:
            return ""
        if not str(image_url).startswith(("http://", "https://")):
            # 本地静态图外网不可达，多模态模型取不到图，直接跳过
            return ""
        try:
            payload = {
                "model": model,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text",
                         "text": "这是房源实拍图。用 2~3 句中文客观描述：户型格局、采光、装修新旧、"
                                 "是否有明显缺陷。不要夸大，不要编造图中没有的信息。"},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }],
                "max_tokens": 300,
            }
            resp = requests.post(
                f"{settings.llm_api_base.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.llm_api_key}",
                         "Content-Type": "application/json"},
                json=payload, timeout=25,
            )
            if resp.status_code != 200:
                logger.warning(f"[看房报告] 视觉模型返回 {resp.status_code}")
                return ""
            data = resp.json()
            return (data["choices"][0]["message"]["content"] or "").strip()[:300]
        except Exception as e:
            logger.warning(f"[看房报告] 视觉模型调用失败: {e}")
            return ""

    # ---------- 事实 → Prompt ----------

    @staticmethod
    def _facts_prompt(facts: dict) -> str:
        d = facts["house"]
        lines = [
            "【房源基本事实】",
            f"- 标题：{d.get('title') or '暂无'}",
            f"- 区域/板块：{d.get('region') or '暂无'} / {d.get('block') or '暂无'}",
            f"- 小区地址：{d.get('address') or '暂无'}",
            f"- 户型：{d.get('rooms') or '暂无'}；面积：{d.get('area') or '暂无'}；"
            f"朝向：{d.get('direction') or '暂无'}；楼层：{d.get('floor') or '暂无'}",
            f"- 装修：{d.get('decoration') or '暂无'}；建成年代：{d.get('build_year') or '暂无'}；"
            f"类型：{d.get('rent_type') or '暂无'}",
            f"- 挂牌总价：{d.get('price') or '暂无'}；折算单价：{facts['unit_price'] or '暂无'} 万/㎡",
        ]

        est = facts.get("estimate") or {}
        market = facts.get("market") or {}
        if est:
            lines += [
                "",
                "【模型估价与区域行情】",
                f"- 模型合理总价区间：{est.get('estimated_price_min')} ~ {est.get('estimated_price_max')} 万"
                f"（中枢 {est.get('estimated_price_avg')} 万，置信度 {est.get('confidence')}）",
                f"- 区域单价中位数：{market.get('median_unit_price') or '暂无'} 万/㎡"
                f"（P25 {market.get('unit_p25')} ~ P75 {market.get('unit_p75')}，样本 {market.get('sample_count')} 套）",
                f"- 区域总价中位数：{market.get('median_total_price') or '暂无'} 万",
                f"- 本房单价在区域中的分位：{market.get('percentile') if market.get('percentile') is not None else '暂无'}",
                f"- 估价依据：{est.get('sample_basis') or '暂无'}；{est.get('advice') or ''}",
            ]
            details = [f"{x.get('label')}{x.get('pct'):+d}%（{x.get('amount'):+.1f} 万）"
                       for x in (est.get("factors_detail") or [])[:5]]
            if details:
                lines.append("- 特征影响：" + "、".join(details))

        trend = facts.get("trend")
        if trend:
            preds = "、".join(f"{p['month']} {p['price']} 万" for p in (trend.get("predictions") or [])[:6])
            lines += [
                "",
                "【区域价格走势（LSTM 预测）】",
                f"- 最新月度均价：{trend.get('latest_price')} 万（{trend.get('latest_month')}）",
                f"- 环比：{trend.get('mom_change')}%；未来 6 个月预测涨跌：{trend.get('predicted_change')}%",
                f"- 逐月预测：{preds or '暂无'}",
                f"- 预测方法：{trend.get('method') or '暂无'}",
            ]

        poi = facts.get("poi") or []
        if poi:
            lines += ["", "【周边配套（高德 POI，直线距离）】"]
            for cat in poi:
                items = "、".join(f"{i['name']} {i['distance']} 米" for i in cat["items"])
                lines.append(f"- {cat['label']}：{items}")
        else:
            lines += ["", "【周边配套】暂无坐标或未配置高德 Key，无法提供周边设施数据。"]

        if facts.get("image_note"):
            lines += ["", "【图片理解（视觉模型）】", f"- {facts['image_note']}"]
        else:
            lines += ["", "【图片理解】未启用视觉模型或图片不可用，本报告不含图片分析。"]

        return "\n".join(lines)

    # ---------- 模板降级 ----------

    @staticmethod
    def _fallback_report(facts: dict) -> str:
        d = facts["house"]
        est = facts.get("estimate") or {}
        market = facts.get("market") or {}
        trend = facts.get("trend") or {}
        price = facts.get("price")
        mid = est.get("estimated_price_avg")

        verdict = "数据不足，建议实地核实后再判断"
        if price and mid:
            gap = price - mid
            if gap > 0:
                verdict = f"挂牌价高于模型中枢约 {gap:.0f} 万，存在议价空间，值得看但不必急"
            else:
                verdict = f"挂牌价低于模型中枢约 {abs(gap):.0f} 万，性价比偏高，建议尽快约看"

        parts = [
            "## 一句话结论",
            f"{d.get('region') or ''} {d.get('rooms') or ''} {d.get('area') or ''}，"
            f"挂牌 {d.get('price') or '暂无'}；{verdict}。",
            "",
            "## 房源与价格诊断",
            f"- 折算单价：{facts.get('unit_price') or '暂无'} 万/㎡；"
            f"区域单价中位数 {market.get('median_unit_price') or '暂无'} 万/㎡。",
            f"- 模型合理总价区间：{est.get('estimated_price_min') or '暂无'} ~ "
            f"{est.get('estimated_price_max') or '暂无'} 万（中枢 {mid or '暂无'} 万）。",
            f"- 估价依据：{est.get('sample_basis') or '同区域样本不足'}。",
            "",
            "## 周边与通勤",
        ]
        if facts.get("poi"):
            for cat in facts["poi"]:
                parts.append(f"- {cat['label']}：" +
                             "、".join(f"{i['name']} {i['distance']} 米" for i in cat["items"]))
        else:
            parts.append("- 暂无坐标或未配置高德 Key，周边设施数据缺失，建议实地确认通勤时间。")

        parts += [
            "",
            "## 价格走势判断",
        ]
        if trend:
            parts.append(f"- {d.get('region') or ''} 最新月均价 {trend.get('latest_price')} 万，"
                         f"环比 {trend.get('mom_change')}%，未来 6 个月预测 {trend.get('predicted_change')}%"
                         f"（{trend.get('method') or 'LSTM'}）。")
        else:
            parts.append("- 该区域走势数据不足，暂时给不出可靠的涨跌判断。")

        parts += [
            "",
            "## 议价与风险提示",
            "- 先核实满五唯一 / 产权年限 / 抵押情况，这三项直接决定税费与过户风险。",
            "- 以同小区近 3 个月成交价为锚，挂牌价高于区域中位数时，首轮可让价 3%~5%。",
            "",
            "## 建议下一步",
            "- 约看时重点看采光、噪音与楼层实际体验，并让业主出示产权证与物业费缴纳记录。",
            "- 需要更细的判断，可再用「房价估算」按你的首付与贷款方案测算月供。",
        ]
        return "\n".join(parts)

    # ---------- 生成 ----------

    async def generate(self, house_id: int, refresh: bool = False) -> Optional[dict]:
        cached = _CACHE.get(house_id)
        if cached and not refresh and time.time() - cached["ts"] < _CACHE_TTL:
            return cached["data"]

        start = time.time()
        facts = self.collect(house_id)
        if not facts:
            return None

        prompt = self._facts_prompt(facts)
        report, model, degraded = "", settings.llm_model_name, False
        try:
            report = (await get_llm_service().generate(prompt, REPORT_SYSTEM_PROMPT) or "").strip()
        except Exception as e:
            logger.warning(f"[看房报告] LLM 生成失败，改用模板: {e}")

        # 模型有时会把整段内容包在 ```markdown 里，前端按纯文本渲染，去掉围栏
        if report.startswith("```"):
            lines = report.splitlines()
            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            report = "\n".join(lines).strip()

        if len(report) < 60:
            # 模型没配置 / 超时 / 输出过短：用模板兜底，保证功能可用
            report = self._fallback_report(facts)
            model, degraded = "template", True

        d = facts["house"]
        data = {
            "status": "ok",
            "house_id": house_id,
            "house": {
                "id": d.get("id"), "title": d.get("title"), "region": d.get("region"),
                "address": d.get("address"), "rooms": d.get("rooms"), "area": d.get("area"),
                "price": d.get("price"), "image_url": d.get("image_url"),
                "unit_price": facts.get("unit_price"),
            },
            "report": report,
            "model": model,
            "degraded": degraded,
            "latency": round(time.time() - start, 2),
            "generated_at": time.strftime("%Y-%m-%d %H:%M"),
            "facts": {
                "unit_price": facts.get("unit_price"),
                "estimate_range": [
                    (facts.get("estimate") or {}).get("estimated_price_min"),
                    (facts.get("estimate") or {}).get("estimated_price_max"),
                ],
                "predicted_change": (facts.get("trend") or {}).get("predicted_change"),
                "poi_categories": len(facts.get("poi") or []),
                "image_analyzed": bool(facts.get("image_note")),
            },
        }
        _CACHE[house_id] = {"ts": time.time(), "data": data}
        logger.info(f"[看房报告] house_id={house_id} model={model} 用时 {data['latency']}s")
        return data
