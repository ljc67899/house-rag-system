# -*- coding: utf-8 -*-
"""数据分析模块路由（模块 A~E）：

- 模块 A：/analysis/ml-predict、/analysis/ml-model     房价智能预测（深度学习回归，PyTorch MLP）
- 模块 B：/analysis/recommend/v2                       用户画像与协同过滤推荐
- 模块 C：/analysis/dashboard                          房产数据可视化分析大屏
- 模块 D：/analysis/sentiment/*                        评论情感分析（NLP）
- 模块 E：/analysis/trend/*                            房价走势时间序列分析（LSTM 深度学习）
- 模块 F：/analysis/viewing-report/{id}                AI 看房报告（多模态 Agent 汇总）
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy.orm import Session
from loguru import logger

from app.database import get_db
from app.models import schemas
from app.services import (
    PricePredictionService,
    CollaborativeService,
    DashboardService,
    SentimentService,
    TrendService,
)
from app.services.viewing_report_service import ViewingReportService

router = APIRouter(prefix="/analysis", tags=["数据分析"])


# ---------- 模块 A：房价智能预测 ----------

@router.post("/ml-predict")
def ml_predict(req: schemas.MLPredictRequest, db: Session = Depends(get_db)):
    """机器学习房价预测"""
    service = PricePredictionService(db)
    try:
        result = service.predict(
            region=req.region, area=req.area, rooms=req.rooms,
            direction=req.direction, rent_type=req.rent_type)
    except Exception as e:
        logger.exception("ML predict failed")
        return {"status": "error", "error": str(e)}
    if result.get("status") == "unavailable":
        return {"status": "error", "error": result.get("error", "模型不可用")}
    return result


@router.get("/ml-model")
def ml_model_info(db: Session = Depends(get_db)):
    """模型信息与评估指标"""
    service = PricePredictionService(db)
    return service.model_info()


# ---------- 模块 B：用户画像与协同过滤推荐 ----------

@router.get("/recommend/v2")
def collab_recommend(
    user_id: int = Query(1, description="用户ID"),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """协同过滤 + 用户画像推荐"""
    try:
        result = CollaborativeService(db).recommend(user_id, limit=limit)
    except Exception as e:
        logger.exception("Collab recommend failed")
        return {"status": "error", "error": str(e)}
    result["status"] = "ok"
    return result


@router.get("/profile/{user_id}")
def user_profile(user_id: int, db: Session = Depends(get_db)):
    """用户画像"""
    try:
        profile = CollaborativeService(db).get_user_profile(user_id)
    except Exception as e:
        logger.exception("Get profile failed")
        return {"status": "error", "error": str(e)}
    profile["status"] = "ok"
    return profile


# ---------- 模块 C：数据可视化大屏 ----------

@router.get("/dashboard")
def dashboard_stats(db: Session = Depends(get_db)):
    """大屏统计聚合"""
    try:
        return {"status": "ok", **DashboardService(db).stats()}
    except Exception as e:
        logger.exception("Dashboard stats failed")
        return {"status": "error", "error": str(e)}


@router.get("/blocks")
def region_blocks(region: str, db: Session = Depends(get_db)):
    """某区域下各板块的截面行情（在挂套数 / 均价 / 相对本区偏离）

    走势页用它做「单区深挖」，与数据分析页的区域横向对比区分开。
    """
    try:
        return DashboardService(db).region_blocks(region)
    except Exception as e:
        logger.exception("Region blocks failed")
        return {"status": "error", "error": str(e)}


# ---------- 模块 D：评论情感分析 ----------

@router.post("/sentiment/analyze")
def sentiment_analyze(req: schemas.SentimentRequest, db: Session = Depends(get_db)):
    """单条文本情感分析"""
    try:
        return {"status": "ok", **SentimentService(db).analyze(req.text)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get("/sentiment/stats")
def sentiment_stats(db: Session = Depends(get_db)):
    """全站评论情感分布"""
    try:
        return {"status": "ok", **SentimentService(db).stats()}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get("/sentiment/house/{house_id}")
def sentiment_house(house_id: int, db: Session = Depends(get_db)):
    """单套房源评论情感"""
    try:
        return {"status": "ok", **SentimentService(db).house_stats(house_id)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get("/sentiment/recent")
def sentiment_recent(limit: int = Query(20, ge=1, le=50), db: Session = Depends(get_db)):
    """最近评论情感分析结果"""
    try:
        return {"status": "ok", "items": SentimentService(db).recent_analyzed(limit)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ---------- 模块 E：房价走势时间序列分析 ----------

@router.get("/trend/regions")
def trend_regions(db: Session = Depends(get_db)):
    """可选区域列表"""
    try:
        return {"status": "ok", "regions": TrendService(db).regions()}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get("/trend/overview")
def trend_overview(db: Session = Depends(get_db)):
    """各区域均价涨跌总览（需在 {region} 之前注册，避免被捕获）"""
    try:
        return {"status": "ok", **TrendService(db).overview()}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get("/trend/{region}")
def trend_region(
    region: str,
    months: int = Query(12, ge=1, le=24, description="预测月数"),
    db: Session = Depends(get_db),
):
    """区域历史走势 + 未来预测"""
    try:
        result = TrendService(db).get_trend(region, predict_months=months)
    except Exception as e:
        logger.exception("Trend failed")
        return {"status": "error", "error": str(e)}
    return result


# ---------- 模块 F：AI 看房报告（多模态 Agent） ----------

@router.get("/viewing-report/{house_id}", summary="AI 看房报告")
async def viewing_report(
    house_id: int,
    refresh: bool = Query(False, description="true 表示忽略缓存重新生成"),
    db: Session = Depends(get_db),
):
    """一键生成《看房报告》

    汇总五类事实后由 LLM 组织成报告：房源事实 / 模型估价与区域行情 /
    LSTM 走势预测 / 高德周边 POI / 可选图片理解。
    工具失败只降级对应小节，LLM 不可用时回退模板，保证接口一定返回内容。
    """
    try:
        result = await ViewingReportService(db).generate(house_id, refresh=refresh)
    except Exception as e:
        logger.exception("Viewing report failed")
        return {"status": "error", "error": str(e)}
    if not result:
        raise HTTPException(status_code=404, detail="房源不存在")
    return result
