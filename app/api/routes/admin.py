# -*- coding: utf-8 -*-
"""后台管理 API（独立 /admin 后台的数据层）

与前台接口彻底分开，统一前缀 /api/admin，全部走 RBAC 权限点：

    概览      house:read
    房源      house:read / house:delete
    用户      user:manage
    评论      comment:manage

设计原则：
    - 后台只做「管理与统计」，不承载业务展示逻辑，前台接口保持原样；
    - 写操作尽量复用 HouseService（新增/删除会同步向量库，避免 AI 检索到已删房源）；
    - 导出走流式 CSV（utf-8-sig，Excel 直接双击不乱码），导入限制行数防止一次灌爆。
"""

import csv
import io
import time
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.database import get_db
from app.models.house import HouseComment, HouseInfo
from app.models.user import UserInfo
from app.services.comment_service import CommentService
from app.services.house_service import HouseService
from app.services.rbac_service import hash_user_password
from app.services.sentiment_service import SentimentService

router = APIRouter(prefix="/admin", tags=["后台管理"])

# 房源图库目录（与前台 <img src> 共用同一批静态文件）
IMAGES_DIR = Path(__file__).resolve().parents[2] / "static" / "images" / "houses"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
IMAGE_MAX_BYTES = 5 * 1024 * 1024

# 概览涉及全表价格解析（3.8 万行），与前台共用 60 秒缓存
_OVERVIEW_TTL = 60
_OVERVIEW_CACHE: dict = {"ts": 0.0, "data": None}

PRICE_BUCKETS = [
    ("300 万以下", 0, 300),
    ("300-500 万", 300, 500),
    ("500-800 万", 500, 800),
    ("800-1200 万", 800, 1200),
    ("1200 万以上", 1200, float("inf")),
]

EXPORT_COLUMNS = [
    "title", "region", "block", "address", "price", "rooms", "area",
    "direction", "decoration", "floor", "build_year", "traffic",
    "rent_type", "publish_date", "image_url",
]

IMPORT_LIMIT = 500
BATCH_DELETE_LIMIT = 500


def _today() -> datetime:
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)


def _parse_wan(price) -> float:
    """把 '728万' / '728' / '7280000元' 统一成「万」数值"""
    if price is None:
        return 0.0
    text = str(price).replace(",", "").strip()
    if not text:
        return 0.0
    try:
        if "亿" in text:
            return float(text.replace("亿", "").replace("元", "")) * 10000
        if "元" in text and "万" not in text:
            return float(text.replace("元", "")) / 10000
        return float(text.replace("万", "").replace("元", ""))
    except ValueError:
        return 0.0


# ---------- 概览 ----------

@router.get("/overview", summary="后台概览统计")
def admin_overview(
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_permission("house:read")),
):
    """后台首页看板：房源 / 用户 / 评论三条主线 + 区域与价格分布"""
    now = time.time()
    if _OVERVIEW_CACHE["data"] and now - _OVERVIEW_CACHE["ts"] < _OVERVIEW_TTL:
        return _OVERVIEW_CACHE["data"]

    today = _today()
    house_total = db.query(func.count(HouseInfo.id)).scalar() or 0
    house_today = db.query(func.count(HouseInfo.id)).filter(
        HouseInfo.created_at >= today).scalar() or 0

    # 价格分布：一次取出全量单价再分桶（3.8 万条字符串解析，比 5 条 SQL 快）
    prices = [p for (p,) in db.query(HouseInfo.price).all()]
    values = [v for v in (_parse_wan(p) for p in prices) if v > 0]
    price_buckets = []
    for name, lo, hi in PRICE_BUCKETS:
        price_buckets.append({
            "name": name,
            "value": sum(1 for v in values if lo <= v < hi),
        })

    regions = HouseService(db).list_regions()[:8]

    user_total = db.query(func.count(UserInfo.id)).scalar() or 0
    user_today = db.query(func.count(UserInfo.id)).filter(
        UserInfo.create_time >= today).scalar() or 0

    comment_total = db.query(func.count(HouseComment.id)).scalar() or 0
    comment_today = db.query(func.count(HouseComment.id)).filter(
        HouseComment.create_time >= today).scalar() or 0
    avg_rating = db.query(func.avg(HouseComment.score)).scalar()

    recent = db.query(HouseInfo).order_by(HouseInfo.id.desc()).limit(6).all()

    data = {
        "houses": {
            "total": int(house_total),
            "today_new": int(house_today),
            "avg_price": round(sum(values) / len(values), 1) if values else 0,
            "regions": len(regions),
        },
        "users": {"total": int(user_total), "today_new": int(user_today)},
        "comments": {
            "total": int(comment_total),
            "today_new": int(comment_today),
            "avg_rating": round(float(avg_rating), 2) if avg_rating else 0,
        },
        "region_distribution": [
            {"name": r["name"], "value": r["count"]} for r in regions
        ],
        "price_distribution": price_buckets,
        "recent_houses": [
            {
                "id": h.id, "title": h.title, "region": h.region,
                "price": h.price, "rooms": h.rooms, "area": h.area,
                "publish_date": h.publish_date,
            }
            for h in recent
        ],
    }
    _OVERVIEW_CACHE["ts"] = now
    _OVERVIEW_CACHE["data"] = data
    return data


# ---------- 房源：批量删除 / 导出 / 导入 ----------

@router.post("/houses/batch-delete", summary="批量删除房源")
def batch_delete_houses(
    payload: dict,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_permission("house:delete")),
):
    """批量删除：逐条走 HouseService，保证同时清理向量库"""
    ids = payload.get("ids") or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="请选择要删除的房源")
    if len(ids) > BATCH_DELETE_LIMIT:
        raise HTTPException(status_code=400, detail=f"单次最多删除 {BATCH_DELETE_LIMIT} 条")

    service = HouseService(db)
    deleted, missing = [], []
    for raw in ids:
        try:
            house_id = int(raw)
        except (TypeError, ValueError):
            continue
        (deleted if service.delete_house(house_id) else missing).append(house_id)

    return {
        "deleted": len(deleted),
        "missing": len(missing),
        "message": f"已删除 {len(deleted)} 条" + (f"，{len(missing)} 条不存在" if missing else ""),
    }


@router.get("/houses/export", summary="导出房源 CSV")
def export_houses(
    keyword: str = Query("", description="标题 / 小区 / 区域模糊匹配"),
    region: str = Query("", description="按区域过滤"),
    limit: int = Query(5000, ge=1, le=20000),
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_permission("house:read")),
):
    """导出当前筛选结果（默认上限 5000 行，避免一次拉全表）"""
    query = db.query(HouseInfo)
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            HouseInfo.title.like(like) | HouseInfo.address.like(like)
            | HouseInfo.region.like(like) | HouseInfo.block.like(like)
        )
    if region:
        query = query.filter(HouseInfo.region == region)
    rows = query.order_by(HouseInfo.id.desc()).limit(limit).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id"] + EXPORT_COLUMNS)
    for h in rows:
        writer.writerow([h.id] + [getattr(h, col, "") or "" for col in EXPORT_COLUMNS])

    # utf-8-sig：Excel 双击打开不乱码
    content = "\ufeff" + buffer.getvalue()
    filename = f"houses_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/houses/import", summary="导入房源 CSV")
async def import_houses(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_permission("house:create")),
):
    """CSV 导入：表头需与导出模板一致，缺少 title 的行会被跳过

    逐条走 HouseService.create_house，每条都会同步写入向量库，
    因此限制单次 500 行——真要灌几万条应该走离线脚本，不是后台按钮。
    """
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gbk")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="文件编码无法识别，请使用 UTF-8 或 GBK")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV 缺少表头")

    rows = list(reader)
    if len(rows) > IMPORT_LIMIT:
        raise HTTPException(status_code=400, detail=f"单次最多导入 {IMPORT_LIMIT} 行，请拆分文件")

    service = HouseService(db)
    created, skipped = 0, 0
    for row in rows:
        data = {k: (v or "").strip() for k, v in row.items() if k and k in EXPORT_COLUMNS}
        if not data.get("title"):
            skipped += 1
            continue
        try:
            service.create_house(data)
            created += 1
        except Exception:
            skipped += 1

    return {
        "created": created,
        "skipped": skipped,
        "message": f"导入完成：新增 {created} 条，跳过 {skipped} 条",
    }


# ---------- 用户管理 ----------

@router.get("/users", summary="用户列表")
def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query("", description="用户名 / 邮箱模糊匹配"),
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_permission("user:manage")),
):
    query = db.query(UserInfo)
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(UserInfo.name.like(like) | UserInfo.email.like(like))

    total = query.count()
    rows = query.order_by(UserInfo.id.desc()).offset(
        (page - 1) * page_size).limit(page_size).all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": u.id,
                "name": u.name,
                "email": u.email,
                "addr": u.addr,
                "create_time": u.create_time.isoformat() if u.create_time else None,
            }
            for u in rows
        ],
    }


@router.post("/users/{user_id}/reset-password", summary="重置用户密码")
def reset_user_password(
    user_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_permission("user:manage")),
):
    new_password = (payload.get("new_password") or "").strip()
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码至少 6 位")

    user = db.query(UserInfo).filter(UserInfo.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    user.password = hash_user_password(user.name or "", new_password)
    db.commit()
    return {"message": f"已重置 {user.name} 的密码"}


@router.delete("/users/{user_id}", summary="删除用户")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_permission("user:manage")),
):
    """删除普通用户。管理员账号在 admin 表，不受此接口影响。"""
    user = db.query(UserInfo).filter(UserInfo.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    name = user.name
    db.delete(user)
    db.commit()
    return {"message": f"已删除用户 {name}"}


# ---------- 评论管理 ----------

def _filter_comments(query, keyword: str = "", house_id: int = 0,
                     comment_id: int = 0, sentiment: str = ""):
    """评论查询的公共筛选：列表 / 重算 / 导出共用一套口径"""
    if keyword:
        cond = HouseComment.content.like(f"%{keyword}%")
        # 输入纯数字时同时按评论 ID 精确匹配，粘一个 ID 进搜索框也能查
        if keyword.isdigit():
            cond = cond | (HouseComment.id == int(keyword))
        query = query.filter(cond)
    if house_id:
        query = query.filter(HouseComment.house_id == house_id)
    if comment_id:
        query = query.filter(HouseComment.id == comment_id)
    if sentiment:
        if sentiment in ("未分析", "unlabeled"):
            query = query.filter(or_(HouseComment.sentiment.is_(None),
                                     HouseComment.sentiment == ""))
        else:
            query = query.filter(HouseComment.sentiment == sentiment)
    return query


@router.get("/comments", summary="评论列表")
def list_comments(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query("", description="评论内容模糊匹配"),
    house_id: int = Query(0, description="只看某套房源的评论"),
    comment_id: int = Query(0, description="按评论 ID 精确查询"),
    sentiment: str = Query("", description="情感筛选：积极 / 中性 / 消极 / 未分析"),
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_permission("comment:manage")),
):
    query = _filter_comments(
        db.query(HouseComment), keyword, house_id, comment_id, sentiment)
    total = query.count()
    rows = query.order_by(HouseComment.id.desc()).offset(
        (page - 1) * page_size).limit(page_size).all()

    # 历史评论入库时没打情感标签：按页懒回填（词典规则毫秒级），打开后台即逐步补齐
    if SentimentService(db).tag_comments(rows):
        db.commit()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": c.id,
                "house_id": c.house_id,
                "user_name": c.user_name,
                "score": c.score,
                "content": c.content,
                "sentiment": c.sentiment,
                "create_time": c.create_time.isoformat() if c.create_time else None,
            }
            for c in rows
        ],
    }


@router.post("/comments/reanalyze", summary="批量补算评论情感")
def reanalyze_comments(
    keyword: str = Query("", description="评论内容模糊匹配"),
    house_id: int = Query(0, description="只处理某套房源的评论"),
    comment_id: int = Query(0, description="只处理某条评论"),
    force: bool = Query(False, description="true 表示已有标签也重算"),
    limit: int = Query(500, ge=1, le=5000),
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_permission("comment:manage")),
):
    """对当前筛选结果补情感标签：默认只补未打标的，force=true 全量重算"""
    query = _filter_comments(db.query(HouseComment), keyword, house_id, comment_id)
    if not force:
        query = query.filter(or_(HouseComment.sentiment.is_(None),
                                 HouseComment.sentiment == ""))
    rows = query.order_by(HouseComment.id.desc()).limit(limit).all()
    svc = SentimentService(db)
    updated = 0
    for c in rows:
        if not (c.content or "").strip():
            continue
        c.sentiment = svc.analyze(c.content)["label"]
        updated += 1
    if updated:
        db.commit()
    return {
        "updated": updated,
        "scanned": len(rows),
        "message": f"已补算 {updated} 条情感标签" if updated else "没有需要补算的评论",
    }


@router.get("/comments/export", summary="导出评论 CSV")
def export_comments(
    keyword: str = Query("", description="评论内容模糊匹配"),
    house_id: int = Query(0, description="只看某套房源的评论"),
    comment_id: int = Query(0, description="按评论 ID 精确查询"),
    sentiment: str = Query("", description="情感筛选：积极 / 中性 / 消极 / 未分析"),
    limit: int = Query(5000, ge=1, le=20000),
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_permission("comment:manage")),
):
    """导出当前筛选结果（含情感标签），默认上限 5000 行"""
    query = _filter_comments(
        db.query(HouseComment), keyword, house_id, comment_id, sentiment)
    rows = query.order_by(HouseComment.id.desc()).limit(limit).all()
    if SentimentService(db).tag_comments(rows):
        db.commit()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "house_id", "user_name", "score",
                     "sentiment", "content", "create_time"])
    for c in rows:
        writer.writerow([
            c.id, c.house_id or "", c.user_name or "", c.score or "",
            c.sentiment or "", c.content or "",
            c.create_time.strftime("%Y-%m-%d %H:%M:%S") if c.create_time else "",
        ])

    # utf-8-sig：Excel 双击打开不乱码
    content = "\ufeff" + buffer.getvalue()
    filename = f"comments_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/comments/{comment_id}", summary="删除评论")
def delete_comment(
    comment_id: int,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_permission("comment:manage")),
):
    comment = db.query(HouseComment).filter(HouseComment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="评论不存在")
    # 先清理该评论下的回复，避免留下孤儿回复
    replies = CommentService(db).delete_replies_of([comment_id])
    db.delete(comment)
    db.commit()
    return {"message": "评论已删除", "deleted_replies": replies}


@router.post("/comments/batch-delete", summary="批量删除评论")
def batch_delete_comments(
    payload: dict,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_permission("comment:manage")),
):
    ids = payload.get("ids") or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="请选择要删除的评论")
    if len(ids) > BATCH_DELETE_LIMIT:
        raise HTTPException(status_code=400, detail=f"单次最多删除 {BATCH_DELETE_LIMIT} 条")

    valid_ids = []
    for raw in ids:
        try:
            valid_ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    replies = CommentService(db).delete_replies_of(valid_ids) if valid_ids else 0
    deleted = db.query(HouseComment).filter(
        HouseComment.id.in_(valid_ids)).delete(synchronize_session=False) if valid_ids else 0
    db.commit()
    return {
        "deleted": int(deleted),
        "deleted_replies": replies,
        "message": f"已删除 {deleted} 条评论",
    }


# ---------- 区域下拉 ----------

@router.get("/house-images", summary="房源图库列表")
def list_house_images(
    _admin: dict = Depends(require_permission("house:read")),
):
    """列出 static/images/houses 下可用的图片，后台编辑房源时做可视化选择"""
    if not IMAGES_DIR.exists():
        return {"items": []}

    items = []
    for path in IMAGES_DIR.iterdir():
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTS:
            continue
        stat = path.stat()
        items.append({
            "name": path.name,
            "url": f"/static/images/houses/{path.name}",
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
        })
    # 新上传的排在前面
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return {"items": items}


@router.post("/house-images", summary="上传房源图片")
async def upload_house_image(
    file: UploadFile = File(...),
    _admin: dict = Depends(require_permission("house:update")),
):
    """上传图片到图库并返回可访问 URL（限制 5MB / 仅常见图片格式）"""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in IMAGE_EXTS:
        raise HTTPException(status_code=400, detail="仅支持 jpg / jpeg / png / webp 图片")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(data) > IMAGE_MAX_BYTES:
        raise HTTPException(status_code=400, detail="图片不能超过 5MB")

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    name = f"house_up_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}{suffix}"
    (IMAGES_DIR / name).write_bytes(data)

    return {
        "name": name,
        "url": f"/static/images/houses/{name}",
        "message": "图片已上传",
    }


@router.get("/regions", summary="后台区域列表（下拉框用）")
def admin_regions(
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_permission("house:read")),
):
    return {"regions": HouseService(db).list_regions()}
