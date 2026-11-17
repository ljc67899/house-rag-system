# -*- coding: utf-8 -*-
"""演示用模拟数据集：

- 模块 B：模拟用户画像与行为交互（收藏 / 浏览 / 对比 / 评论），供协同过滤推荐使用
- 模块 D：模拟带评分的房源评论，供情感分析使用
- 模块 E：模拟区域月度房价走势序列，供时间序列预测使用

所有写入均为幂等（已有足够数据时跳过），不影响真实业务数据。
"""

import random
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from loguru import logger
from sqlalchemy import func, insert
from sqlalchemy.orm import Session

from app.models.house import (
    HouseInfo, HouseComment, HouseRecommend, HouseCompare, BrowseHistory,
)
from app.models.user import UserInfo
from app.models.trend import HousePriceTrend
from app.services.rbac_service import hash_user_password

# ---------- 通用工具 ----------

_price_re = None


def _parse_price_wan(price_str) -> float:
    """解析 '585万' / '585万元' -> 585.0；失败返回 None"""
    if not price_str:
        return None
    s = str(price_str).strip()
    s = s.replace("万元", "").replace("万", "").replace(",", "").replace("，", "")
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _parse_area(area_str) -> float:
    if not area_str:
        return None
    s = str(area_str).strip().replace("平米", "").replace("㎡", "").replace("平", "")
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _parse_rooms(rooms_str):
    """'3室2厅1卫' -> (3, 2, 1)"""
    if not rooms_str:
        return 0, 0, 0
    s = str(rooms_str)
    def _n(k):
        import re
        m = re.search(r"(\d+)" + k, s)
        return int(m.group(1)) if m else 0
    return _n("室"), _n("厅"), _n("卫")


def _iter_houses(db: Session):
    """加载全部房源（只取需要的字段）"""
    return db.query(HouseInfo).all()


# ---------- 模块 B：模拟用户与行为交互 ----------

PROFILES = [
    {"label": "刚需自住", "price_range": (200, 500), "regions": ["昌平区", "大兴区", "通州区", "顺义区", "房山区"], "rooms": [2, 3]},
    {"label": "改善换房", "price_range": (500, 1200), "regions": ["朝阳区", "海淀区", "丰台区"], "rooms": [3, 4]},
    {"label": "学区刚需", "price_range": (300, 900), "regions": ["海淀区", "西城区", "东城区"], "rooms": [1, 2, 3]},
    {"label": "高端置业", "price_range": (900, 3000), "regions": ["朝阳区", "西城区", "东城区", "海淀区"], "rooms": [3, 4, 5]},
    {"label": "首次置业", "price_range": (150, 450), "regions": ["丰台区", "大兴区", "昌平区", "门头沟区"], "rooms": [1, 2]},
]

POSITIVE_WORDS = [
    "精装修，性价比高，位置极佳，采光充足，户型方正，交通便利，配套成熟，"
    "安静舒适，视野开阔，格局好，拎包入住，物业管理好，小区环境优美",
]
NEUTRAL_WORDS = [
    "小区一般，价格中等，位置尚可，户型常规，周边配套基本齐全，楼层适中",
]
NEGATIVE_WORDS = [
    "装修老旧，价格偏高，位置偏远，采光一般，户型不佳，交通不便，配套欠缺，"
    "楼层较差，临街吵闹，物业一般，房龄较老",
]


def seed_users_and_interactions(db: Session, count: int = 40):
    """生成模拟用户及行为交互数据（收藏/浏览/对比/评论）"""
    # 已有足够交互用户则跳过
    interactive = db.query(BrowseHistory.user_id).distinct().count()
    if interactive >= 20:
        logger.info("Simulated interactions already exist, skip")
        return

    houses = _iter_houses(db)
    if not houses:
        logger.warning("No houses, skip interaction seeding")
        return
    # 过滤有效房源
    valid = []
    for h in houses:
        p = _parse_price_wan(h.price)
        a = _parse_area(h.area)
        if p and a and 50 < p < 5000 and 20 <= a <= 400:
            valid.append(h)
    if not valid:
        logger.warning("No valid houses for interaction seeding")
        return
    rng = random.Random(20260825)

    now = datetime.now()
    existing_count = db.query(UserInfo).count()
    created = 0
    for i in range(count):
        name = f"user{i + 1:02d}"
        if db.query(UserInfo).filter(UserInfo.name == name).first():
            continue
        profile = rng.choice(PROFILES)
        db.add(UserInfo(
            # 模拟用户统一密码 123456，入库前按用户名称加盐哈希（不再存明文）
            name=name, password=hash_user_password(name, "123456"),
            email=f"{name}@example.com",
            addr=rng.choice(profile["regions"]) + "某小区",
            create_time=now,
        ))
        created += 1
    db.commit()
    logger.info(f"Created {created} simulated users")

    # 为用户生成行为
    users = db.query(UserInfo).filter(UserInfo.name.like("user%")).all()
    fav_existing = db.query(HouseRecommend).filter(HouseRecommend.score == -1).count()
    if fav_existing >= 150:
        logger.info("Favorites already exist, skip behaviors")
        return

    favorites, browses, compares, comments = [], [], [], []
    for u in users:
        profile = rng.choice(PROFILES)
        lo, hi = profile["price_range"]
        pool = [h for h in valid
                if h.region in profile["regions"]
                and lo <= (_parse_price_wan(h.price) or 0) <= hi
                and _parse_rooms(h.rooms)[0] in profile["rooms"]]
        if not pool:
            pool = valid
        rng.shuffle(pool)
        picks = pool[:rng.randint(3, 8)]
        for h in picks:
            favorites.append((u.id, h))
        # 浏览
        rng.shuffle(valid)
        for h in valid[:rng.randint(6, 16)]:
            browses.append((u.id, h, now - timedelta(days=rng.randint(0, 60), hours=rng.randint(0, 23))))
        # 对比
        rng.shuffle(valid)
        for h in valid[:rng.randint(2, 5)]:
            compares.append((u.name, h))
        # 评论
        rng.shuffle(valid)
        for h in valid[:rng.randint(1, 5)]:
            score = rng.choice([3, 4, 4, 5, 5, 2])
            comments.append((u.id, u.name, h, float(score)))

    for uid, h in favorites:
        db.add(HouseRecommend(user_id=uid, house_id=h.id, title=h.title,
                              address=h.address, block=h.block, score=-1))
    for uid, h, t in browses:
        db.add(BrowseHistory(user_id=uid, house_id=h.id, house_title=h.title,
                             house_price=h.price, house_address=h.address, view_time=t))
    for uname, h in compares:
        db.add(HouseCompare(user_name=uname, house_id=h.id, add_time=now - timedelta(days=rng.randint(0, 30))))
    for uid, uname, h, score in comments:
        db.add(HouseComment(house_id=h.id, user_id=uid, user_name=uname,
                            score=score, content=_gen_comment(score, h), create_time=now - timedelta(days=rng.randint(0, 60))))
    db.commit()
    logger.info(f"Seeded interactions: favorites={len(favorites)}, browses={len(browses)}, "
                f"compares={len(compares)}, comments={len(comments)}")


def _gen_comment(score: float, house) -> str:
    if score >= 4:
        words = POSITIVE_WORDS[0].split("，")
        n = random.randint(3, 5)
        return f"{random.choice(words)}，{random.choice(words)}。房东人很好，{random.choice(words)}，整体满意，推荐！"
    if score == 3:
        return f"房子{random.choice(NEUTRAL_WORDS[0].split('，'))}，住着还行，没什么大问题，也没特别惊喜。"
    return f"入住后感觉{random.choice(NEGATIVE_WORDS[0].split('，'))}，{random.choice(NEGATIVE_WORDS[0].split('，'))}，体验一般，不推荐。"


# ---------- 模块 D：补充评论量 ----------

def seed_comments(db: Session, target: int = 1200):
    """评论不足时生成模拟评论，供情感分析使用"""
    count = db.query(HouseComment).count()
    if count >= target:
        logger.info(f"Comments already enough ({count}), skip")
        return
    houses = _iter_houses(db)
    users = db.query(UserInfo).filter(UserInfo.name.like("user%")).all()
    if not houses or not users:
        logger.warning("No houses/users for comment seeding")
        return
    rng = random.Random(20260825)
    now = datetime.now()
    added = 0
    for i in range(count, target):
        h = rng.choice(houses)
        u = rng.choice(users)
        score = rng.choices([5, 4, 3, 2, 1], weights=[38, 32, 16, 8, 6])[0]
        db.add(HouseComment(house_id=h.id, user_id=u.id, user_name=u.name,
                            score=score, content=_gen_comment(score, h),
                            create_time=now - timedelta(days=rng.randint(0, 120))))
        added += 1
    db.commit()
    logger.info(f"Seeded {added} more comments")


# ---------- 模块 E：模拟房价走势序列 ----------

def seed_price_trends(db: Session, months_back: int = 78):
    """生成 2019-01 ~ 至今 的区域月度均价序列（末端锚定当前挂牌均价）"""
    if db.query(HousePriceTrend).count() > 0:
        logger.info("Price trends already exist, skip")
        return

    houses = _iter_houses(db)
    # 统计各区域当前均价
    from collections import defaultdict
    region_prices = defaultdict(list)
    for h in houses:
        p = _parse_price_wan(h.price)
        r = h.region
        if p and 50 < p < 5000 and r:
            region_prices[r].append(p)
    if not region_prices:
        logger.warning("No region prices, skip trend seeding")
        return
    # 取房源量 Top 12 的区域
    top_regions = sorted(region_prices.items(), key=lambda kv: -len(kv[1]))[:12]

    rng = random.Random(20260825)
    now = datetime.now()
    # 用 relativedelta 保证按自然月推移，避免 30 天近似导致月份重复
    months = []
    for k in range(months_back, -1, -1):
        ym = now - relativedelta(months=k)
        months.append(ym.strftime("%Y-%m"))

    # 全国大盘趋势（温和上涨 + 周期波动）
    for region, prices in top_regions:
        cur = sum(prices) / len(prices)
        base = cur / 1.42  # 回溯起点约为当前价的 70%
        for idx, month in enumerate(months):
            trend = 1.0 + 0.42 * (idx / max(len(months) - 1, 1))          # 长期上涨
            season = 1.0 + 0.05 * _seasonal(idx)                            # 季节性波动
            noise = 1.0 + rng.uniform(-0.03, 0.03)                          # 随机噪声
            val = base * trend * season * noise
            db.add(HousePriceTrend(region=region, month=month,
                                   avg_price=round(val, 2),
                                   count=rng.randint(20, 120),
                                   source="simulated"))
    db.commit()
    logger.info(f"Seeded price trends for {len(top_regions)} regions x {len(months)} months")


def _seasonal(idx: int) -> float:
    """季节因子：金九银十、年初淡季"""
    m = idx % 12
    if m in (8, 9):       # 9、10 月
        return 1.05
    if m in (0, 1):       # 1、2 月
        return 0.96
    if m in (5, 6):
        return 1.03
    return 1.0


def _month_seasonal(month: int) -> float:
    """按月季节因子（相对上月涨跌幅）：金九银十走强，年初与盛夏走弱"""
    return {
        1: -0.015, 2: -0.025, 3: 0.010, 4: 0.015, 5: 0.010, 6: 0.000,
        7: -0.010, 8: -0.005, 9: 0.030, 10: 0.025, 11: 0.005, 12: 0.000,
    }.get(int(month), 0.0)


def extend_price_trends_to_now(db: Session) -> int:
    """把各区域月度均价序列补齐到「当前月份」

    数据是相对生成时间造的，放一段时间后末端就会停在过去，
    「未来 12 个月预测」就会落在已经过去的月份上。服务启动时调用本函数，
    用近期年化增速 + 月度季节因子 + 小幅噪声把缺失月份补上，
    保证历史序列的末端始终是本月，预测永远面向未来。
    """
    cur_month = datetime.now().strftime("%Y-%m")
    # 已经补到本月就直接返回，避免每次查询都逐区域扫一遍
    latest_overall = db.query(func.max(HousePriceTrend.month)).scalar()
    if latest_overall and latest_overall >= cur_month:
        return 0

    regions = [r[0] for r in db.query(HousePriceTrend.region).distinct().all() if r[0]]
    added = 0

    for region in regions:
        rows = (db.query(HousePriceTrend)
                .filter(HousePriceTrend.region == region)
                .order_by(HousePriceTrend.month.desc()).limit(13).all())
        if not rows:
            continue
        rows = sorted(rows, key=lambda r: r.month)
        last = rows[-1]
        if last.month >= cur_month:
            continue

        prices = [float(r.avg_price) for r in rows if r.avg_price]
        if len(prices) >= 3 and prices[0] > 0:
            # 近 12 个月的平均月增速，裁剪到 ±2%，避免长间隔外推跑飞
            per_month = (prices[-1] / prices[0]) ** (1 / (len(prices) - 1)) - 1
        else:
            per_month = 0.002
        per_month = max(-0.02, min(0.02, per_month))

        base_dt = datetime.strptime(last.month + "-01", "%Y-%m-%d")
        prev = float(last.avg_price or prices[-1] or 0)
        for gap in range(1, 61):
            nxt = base_dt + relativedelta(months=gap)
            month = nxt.strftime("%Y-%m")
            if month > cur_month:
                break
            rng = random.Random(f"{region}-{month}")
            prev = prev * (1 + per_month) * (1 + _month_seasonal(nxt.month)) \
                * (1 + rng.uniform(-0.012, 0.012))
            db.add(HousePriceTrend(
                region=region, month=month, avg_price=round(prev, 2),
                count=max(5, int((last.count or 60) * (1 + rng.uniform(-0.08, 0.08)))),
                source="simulated",
            ))
            added += 1

    if added:
        db.commit()
        logger.info(f"Extended price trends to {cur_month}: +{added} rows")
    return added


# ---------- 模块 D2：按房源补齐评论（保证详情页能看到其他用户的评价） ----------

_POS_OPEN = [
    "在{region}连看了几套，这套{rooms}最合心意",
    "同一片区对比下来，{region}这套性价比高",
    "这套{rooms}整体比照片还好",
    "朋友推荐来看的，{region}这套让人满意",
    "周末专门过来看房，{rooms}比预期宽敞",
]
_POS_BODY = [
    "采光通透，白天基本不用开灯",
    "格局方正，空间利用得很到位",
    "楼层适中，视野比较开阔",
    "装修干净清爽，拎包入住没问题",
    "楼下就是超市和公交站，通勤方便",
    "小区安静，晚上休息不受影响",
    "南北通透，通风好，住着应该舒适",
    "厨卫都有窗，不返潮",
]
_POS_END = [
    "房东报价也实在，准备再去看一次。",
    "已经收藏了，等家人有空一起复看。",
    "整体挺满意，值得推荐。",
    "约了周末复看，希望还能谈谈价。",
    "住着应该舒服，考虑入手。",
]
_NEU_OPEN = [
    "{region}这个位置还行",
    "看完感觉中规中矩",
    "房子没有明显硬伤",
    "同价位里算常见的水平",
    "适合过渡住几年",
]
_NEU_BODY = [
    "{rooms}属于常规格局",
    "采光要看具体楼层",
    "装修是几年前的，需要简单收拾",
    "周边配套基本够用",
    "早晚高峰{region}路上会有点堵",
]
_NEU_END = [
    "可以再对比几套。",
    "价格还能再谈谈。",
    "不算惊喜，也不踩雷。",
    "再考虑一下。",
    "看个人需求。",
]
_NEG_OPEN = [
    "{region}位置偏了点",
    "这套和预期差距不小",
    "现场看比照片差一些",
    "楼层和朝向都一般",
    "临街，白天有些吵",
]
_NEG_BODY = [
    "{rooms}实际看比照片小",
    "进深太长，采光差",
    "装修老旧，重新做得花不少钱",
    "楼下停车位紧张",
    "同价位在{region}能选的更多",
]
_NEG_END = [
    "价格偏高，暂时不考虑。",
    "整体不推荐。",
    "再看看吧，有点失望。",
    "性价比一般，不划算。",
]


def _gen_house_comment(score: float, region: str, rooms: str, decoration: str,
                       traffic: str, rng: random.Random) -> str:
    """按房源属性组合生成评论文本

    三段式拼装（开头 + 细节 + 结尾），同一套房的几条评价不易雷同；
    用词与情感分析词典保持同向，且只拼接真实存在的装修/交通信息，
    避免出现“交通便利不太方便”这类病句。
    """
    ctx = {"region": region or "这个片区", "rooms": rooms or "户型"}
    if score >= 4:
        return "{open}，{body}，{end}".format(
            open=rng.choice(_POS_OPEN).format(**ctx),
            body=rng.choice(_POS_BODY).format(**ctx),
            end=rng.choice(_POS_END),
        )
    if score == 3:
        return "{open}，{body}，{end}".format(
            open=rng.choice(_NEU_OPEN).format(**ctx),
            body=rng.choice(_NEU_BODY).format(**ctx),
            end=rng.choice(_NEU_END),
        )
    return "{open}，{body}，{end}".format(
        open=rng.choice(_NEG_OPEN).format(**ctx),
        body=rng.choice(_NEG_BODY).format(**ctx),
        end=rng.choice(_NEG_END),
    )


def ensure_house_comment_coverage(db: Session, min_per_house: int = 3,
                                  max_per_house: int = 5) -> int:
    """保证每套房源至少有 min_per_house 条评论。

    历史模拟数据是「随机房源撒点」，38267 套房源只覆盖了 1000 多套，
    导致详情页几乎只能看到自己刚发的评价。这里按房源维度补齐，
    让每套房子都有若干其他用户的评价可看；已满足的房源直接跳过（幂等）。
    """
    # 先做轻量判断，已覆盖时不必把 3.8 万条房源属性读出来
    total = db.query(func.count(HouseComment.id)).scalar() or 0
    house_total = db.query(func.count(HouseInfo.id)).scalar() or 0
    if house_total and total >= house_total * min_per_house:
        logger.info(f"House comments already cover every house ({total}), skip")
        return 0

    houses = db.query(
        HouseInfo.id, HouseInfo.region, HouseInfo.rooms,
        HouseInfo.decoration, HouseInfo.traffic,
    ).all()
    if not houses:
        return 0

    counts = dict(
        db.query(HouseComment.house_id, func.count(HouseComment.id))
        .group_by(HouseComment.house_id).all()
    )
    users = db.query(UserInfo.id, UserInfo.name).filter(UserInfo.name.like("user%")).all()
    if not users:
        logger.warning("No simulated users, skip comment coverage")
        return 0

    rng = random.Random(20261020)
    now = datetime.now()
    rows = []
    for hid, region, rooms, decoration, traffic in houses:
        have = counts.get(hid, 0)
        if have >= min_per_house:
            continue
        need = rng.randint(min_per_house, max_per_house) - have
        for _ in range(need):
            uid, uname = rng.choice(users)
            score = rng.choices([5, 4, 3, 2, 1], weights=[42, 30, 15, 7, 6])[0]
            rows.append({
                "house_id": hid,
                "user_id": uid,
                "user_name": uname,
                "score": float(score),
                "content": _gen_house_comment(score, region, rooms, decoration, traffic, rng),
                # 时间打散，避免详情页出现一屏同一时刻的评价
                "create_time": now - timedelta(
                    days=rng.randint(0, 240), hours=rng.randint(0, 23),
                    minutes=rng.randint(0, 59),
                ),
            })

    if not rows:
        return 0

    # 10 万级数据用 executemany 批量写入，避免逐条 add 的 ORM 开销
    for i in range(0, len(rows), 5000):
        db.execute(insert(HouseComment), rows[i:i + 5000])
    db.commit()
    logger.info(f"Backfilled {len(rows)} house comments to cover {len(houses)} houses")
    return len(rows)


def seed_all_simulated(db: Session):
    """统一入口：幂等地补齐模拟数据集"""
    seed_users_and_interactions(db)
    seed_comments(db)
    ensure_house_comment_coverage(db)
    seed_price_trends(db)
    # 时序数据随时间推进会「过期」，每次启动补齐到本月，保证预测面向未来
    extend_price_trends_to_now(db)
