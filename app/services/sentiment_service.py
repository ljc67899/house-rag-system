# -*- coding: utf-8 -*-
"""模块 D：评论情感分析（NLP）

基于中文情感词典的轻量情感分析：
- 积极词 / 消极词 词典打分
- 否定词翻转、程度副词加权
- 输出：情感分 [-1, 1]、情感类别（积极/中性/消极）、关键情感词
"""

import re
from collections import Counter
from sqlalchemy.orm import Session

from app.models.house import HouseComment, HouseInfo

# 说明：只保留「不可再拆分」的核心情感词。采光好/通风好/物业好/态度差/装修差…
# 这类「维度词 + 极性词」的复合词由 ASPECT_KEYWORDS + 单字极性词组合覆盖，
# 不单独穷举，词表因此从 60+ 收敛到 40 以内，也避免了子串重复计分。
POSITIVE_WORDS = [
    "满意", "推荐", "喜欢", "不错", "优秀", "舒适", "安静", "干净", "明亮", "宽敞",
    "便利", "方便", "实惠", "温馨", "完美", "高端", "精致", "理想", "给力", "大气",
    "优雅", "漂亮", "心动", "超值", "安心", "放心", "靠谱", "贴心", "一流", "新颖",
    "划算", "称心", "愉快", "开心", "满足", "值得", "性价比", "通透", "赞", "棒", "好",
]
NEGATIVE_WORDS = [
    "失望", "后悔", "不满意", "糟糕", "垃圾", "脏乱", "潮湿", "阴冷", "破旧", "老旧",
    "老化", "漏水", "发霉", "异味", "噪音", "噪声", "麻烦", "别扭", "难受", "焦虑",
    "担心", "害怕", "不安全", "一般", "中等", "平平", "凑合", "将就", "不如", "偏高",
    "不划算", "不值", "拥挤", "慎入", "避雷", "踩坑", "不推荐", "差", "吵", "贵", "坑", "骗",
]

# 单字极性词极易误伤（"好"→好像/只好，"贵"→贵阳，"差"→差别），
# 命中位置落在下面这些固定搭配里就不计分，这是「不做分词」的兜底手段。
SINGLE_CHAR_GUARD = {
    "好": ("好像", "只好", "好在", "刚好", "好处", "好多"),
    "差": ("差别", "差异", "出差", "时差", "温差"),
    "贵": ("贵阳", "贵州", "贵公司", "贵方"),
    "坑": ("坑洼",),
    "棒": ("棒球",),
    "赞": ("赞助",),
}

POS_SORTED = tuple(sorted(POSITIVE_WORDS, key=len, reverse=True))
NEG_SORTED = tuple(sorted(NEGATIVE_WORDS, key=len, reverse=True))
NEG_SET = frozenset(NEGATIVE_WORDS)
# 否定词后面跟的是哪个情感词：同样长词优先（"不满意"先于"满意"）
ALL_SORTED = tuple(sorted(set(POSITIVE_WORDS) | set(NEGATIVE_WORDS), key=len, reverse=True))
NEGATE_WORDS = ["不", "没", "无", "未", "别", "莫", "非", "不要", "没有", "不太", "不怎么", "不算", "并非"]

# 评价维度词表：把「好用/不好用」落到具体维度上，供详情页口碑摘要展示
ASPECT_KEYWORDS = {
    "采光": ["采光", "明亮", "朝南", "向阳", "通透"],
    "通风": ["通风"],
    "交通": ["交通", "通勤", "地铁", "公交", "出行"],
    "价格": ["价格", "报价", "价位", "性价比", "划算", "总价", "首付"],
    "装修": ["装修", "精装", "简装", "拎包"],
    "户型": ["户型", "格局", "方正", "进深", "面积"],
    "楼层": ["楼层", "高楼", "低楼"],
    "小区环境": ["小区", "物业", "绿化", "环境"],
    "噪音": ["吵", "噪音", "噪声", "临街"],
    "配套": ["配套", "超市", "学校", "医院", "商场"],
}
DEGREE_WORDS = {
    "非常": 1.8, "十分": 1.8, "特别": 1.7, "超": 1.6, "极其": 1.9, "太": 1.5, "很": 1.4,
    "挺": 1.3, "比较": 1.2, "有点": 0.7, "稍微": 0.7, "些许": 0.6, "不太": 0.5, "略": 0.8,
}


class SentimentService:
    """评论情感分析"""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _norm(w: str) -> str:
        return w.strip().replace("！", "").replace("。", "").replace("，", "").replace("、", "")

    @staticmethod
    def _guard(text: str, start: int, word: str) -> bool:
        """单字词命中位置是否落在固定搭配里（"好像"里的"好"、"贵阳"里的"贵"）"""
        for bad in SINGLE_CHAR_GUARD.get(word, ()):
            p = text.find(bad)
            while p >= 0:
                if p <= start < p + len(bad):
                    return True
                p = text.find(bad, p + 1)
        return False

    @classmethod
    def _scan(cls, text: str, words: tuple) -> list:
        """长词优先匹配：命中的字区间打标记，短词不再重复吃同一段文字

        - "不划算" 先于 "划算" 命中，避免正负分同时计入；
        - "采光好" 这类复合词不必穷举，由 "好" 命中即可；
        - 单字词命中落在固定搭配中则跳过，降低不做分词带来的误伤。
        """
        taken = bytearray(len(text))
        hits = []
        for w in words:
            start = 0
            while True:
                i = text.find(w, start)
                if i < 0:
                    break
                start = i + 1
                if any(taken[i:i + len(w)]):
                    continue
                if len(w) == 1 and cls._guard(text, i, w):
                    continue
                for k in range(i, i + len(w)):
                    taken[k] = 1
                hits.append(w)
        return hits

    def analyze(self, text: str) -> dict:
        """单条文本情感分析"""
        text = str(text or "")
        pos_hits = self._scan(text, POS_SORTED)
        neg_hits = self._scan(text, NEG_SORTED)
        score = float(len(pos_hits) - len(neg_hits))
        # 否定词翻转 + 程度副词加权
        degree = 1.0
        for i, ch in enumerate(text):
            for d, mult in DEGREE_WORDS.items():
                if text.startswith(d, i):
                    degree = mult
                    break
            for neg in NEGATE_WORDS:
                if text.startswith(neg, i):
                    # 检查否定词后是否紧跟情感词
                    tail = text[i + len(neg): i + len(neg) + 6]
                    hit = next((w for w in ALL_SORTED if w in tail), None)
                    if hit:
                        sign = -1 if hit in NEG_SET else 1
                        if sign * score > 0 or score == 0:
                            score -= sign * degree
        total = len(pos_hits) + len(neg_hits)
        if total:
            score = score / max(total, 1) * 2  # 归一化到约 [-1,1]
        score = max(-1.0, min(1.0, round(score, 3)))
        if score > 0.2:
            label = "积极"
        elif score < -0.2:
            label = "消极"
        else:
            label = "中性"
        return {
            "text": text[:200],
            "score": score,
            "label": label,
            "positive_words": list(dict.fromkeys(pos_hits))[:8],
            "negative_words": list(dict.fromkeys(neg_hits))[:8],
        }

    def tag_comments(self, comments) -> int:
        """给缺情感标签的评论补标（就地修改，不 commit；返回补了几条）

        新评论入库时已经打标（见 CommentService.add_comment），这里只兜历史数据：
        后台列表、前台评论列表都会调一次，翻到哪补到哪，不需要全量回填脚本。
        """
        filled = 0
        for cm in comments or []:
            if cm is None or cm.sentiment or not (cm.content or "").strip():
                continue
            cm.sentiment = self.analyze(cm.content)["label"]
            filled += 1
        return filled

    def stats(self, sample_limit: int = 12000) -> dict:
        """评论情感分布统计

        评论量已达十万级，词典逐条分析全表需要十几秒；
        这里按最新评论抽样计算分布（比例稳定），total 仍返回真实总量。
        """
        from sqlalchemy import func
        total = self.db.query(func.count(HouseComment.id)).scalar() or 0
        comments = self.db.query(HouseComment).order_by(
            HouseComment.id.desc()).limit(sample_limit).all()
        labels = Counter()
        scores = []
        total_score = 0.0
        for cm in comments:
            r = self.analyze(cm.content)
            labels[r["label"]] += 1
            scores.append(r["score"])
            total_score += cm.score or 0
        n = len(comments)
        return {
            "total": total,
            "sampled": n,
            "distribution": [
                {"name": k, "value": labels.get(k, 0)} for k in ("积极", "中性", "消极")
            ],
            "positive_ratio": round(labels.get("积极", 0) / n * 100, 1) if n else 0,
            "neutral_ratio": round(labels.get("中性", 0) / n * 100, 1) if n else 0,
            "negative_ratio": round(labels.get("消极", 0) / n * 100, 1) if n else 0,
            "avg_sentiment": round(sum(scores) / n, 3) if n else 0,
            "avg_rating": round(total_score / n, 2) if n else 0,
        }

    def house_stats(self, house_id: int) -> dict:
        """单套房源评论情感 + 维度归因（详情页口碑摘要数据源）"""
        comments = self.db.query(HouseComment).filter(
            HouseComment.house_id == house_id).all()
        results = [self.analyze(cm.content) for cm in comments]
        labels = Counter(r["label"] for r in results)
        n = len(results)

        # 维度归因：按整条评价的极性，把命中的维度分别记到好评/差评两侧
        pos_aspects, neg_aspects = Counter(), Counter()
        for cm, r in zip(comments, results):
            text = str(cm.content or "")
            for aspect, keys in ASPECT_KEYWORDS.items():
                if not any(k in text for k in keys):
                    continue
                if r["label"] == "积极":
                    pos_aspects[aspect] += 1
                elif r["label"] == "消极":
                    neg_aspects[aspect] += 1

        highlights = [{"name": k, "count": v} for k, v in pos_aspects.most_common(4)]
        issues = [{"name": k, "count": v} for k, v in neg_aspects.most_common(4)]
        pos_ratio = round(labels.get("积极", 0) / n * 100, 1) if n else 0
        neu_ratio = round(labels.get("中性", 0) / n * 100, 1) if n else 0
        neg_ratio = round(labels.get("消极", 0) / n * 100, 1) if n else 0
        neg_count = labels.get("消极", 0)

        if not n:
            label, summary = "暂无评价", "还没有人评价这套房子，欢迎看完后留下真实感受。"
        else:
            if pos_ratio >= 70:
                label = "口碑很好"
            elif pos_ratio >= 50:
                label = "口碑不错"
            elif neg_ratio >= 40:
                label = "争议较大"
            else:
                label = "口碑一般"
            if not labels.get("积极", 0):
                # 全是中性/消极：不再重复“N 条偏负面”，直接给差评维度
                parts = [f"{n} 条评价均偏负面" if neg_count else f"{n} 条评价偏中性"]
                if issues:
                    parts.append(
                        "主要提到" + "、".join(i["name"] for i in issues[:3])
                        + "，看房时建议重点确认"
                    )
                else:
                    parts.append("建议结合原文再判断")
            else:
                parts = [
                    f"{n} 条评价全部为正面" if pos_ratio >= 100
                    else f"{n} 条评价中 {pos_ratio:g}% 为积极"
                ]
                if highlights:
                    parts.append("好评集中在" + "、".join(h["name"] for h in highlights[:3]))
                if neg_count:
                    if issues:
                        parts.append(
                            f"{neg_count} 条偏负面，主要提到"
                            + "、".join(i["name"] for i in issues[:3])
                            + "，看房时建议重点确认"
                        )
                    else:
                        parts.append(f"{neg_count} 条偏负面，建议结合原文再判断")
            summary = "；".join(parts) + "。"

        avg_rating = round(
            sum((cm.score or 0) for cm in comments) / n, 2) if n else 0
        return {
            "house_id": house_id,
            "total": n,
            "positive_count": labels.get("积极", 0),
            "neutral_count": labels.get("中性", 0),
            "negative_count": neg_count,
            "positive_ratio": pos_ratio,
            "neutral_ratio": neu_ratio,
            "negative_ratio": neg_ratio,
            "distribution": [
                {"name": k, "value": labels.get(k, 0)} for k in ("积极", "中性", "消极")
            ],
            "avg_sentiment": round(sum(r["score"] for r in results) / n, 3) if n else 0,
            "avg_rating": avg_rating,
            "highlights": highlights,
            "issues": issues,
            "label": label,
            "summary": summary,
            "comments": results[:10],
        }

    def recent_analyzed(self, limit: int = 20) -> list:
        """最近评论情感分析结果（供页面展示）"""
        comments = self.db.query(HouseComment).order_by(
            HouseComment.id.desc()).limit(limit).all()
        out = []
        for cm in comments:
            r = self.analyze(cm.content)
            house = self.db.query(HouseInfo).filter(HouseInfo.id == cm.house_id).first()
            r["user_name"] = cm.user_name
            r["house_title"] = house.title if house else f"房源#{cm.house_id}"
            r["rating"] = cm.score
            out.append(r)
        return out
