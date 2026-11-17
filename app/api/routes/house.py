# -*- coding: utf-8 -*-
"""房源管理 API"""

from typing import Optional
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException, Query, status
from app.models.schemas import (
    HouseInfo, HouseListResponse, HouseSearchRequest,
    HouseCreateRequest, HouseUpdateRequest,
)
from app.services.comment_service import CommentService
from app.services.sentiment_service import SentimentService
from app.services.external_geo_service import get_external_geo_service
from app.services.house_service import HouseService
from app.services.notification_service import NotificationService
from app.models.house import HouseInfo
from app.services.index_sync import IndexSyncService
from app.api.deps import (
    get_house_service, require_permission, require_user, require_login, optional_user,
)
from app.database import get_db

router = APIRouter(prefix="/houses", tags=["房源管理"])


@router.get("", summary="获取房源列表")
async def get_house_list(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    keyword: Optional[str] = None,
    region: Optional[str] = None,
    block: Optional[str] = None,
    rooms: Optional[str] = None,
    price_min: Optional[float] = None,
    price_max: Optional[float] = None,
    db: Session = Depends(get_db),
):
    """获取房源列表，支持关键词、区域、户型筛选"""
    service = HouseService(db)
    houses, total = service.get_house_list(
        page=page, page_size=page_size,
        keyword=keyword, region=region, block=block, rooms=rooms,
        price_min=price_min, price_max=price_max,
    )
    house_dicts = []
    for h in houses:
        if hasattr(h, 'to_dict'):
            house_dicts.append(h.to_dict())
        else:
            house_dicts.append({
                'id': h.id, 'title': h.title, 'rooms': h.rooms, 'area': h.area,
                'price': h.price, 'direction': h.direction, 'rent_type': h.rent_type,
                'region': h.region, 'block': h.block, 'address': h.address,
                'traffic': h.traffic, 'facilities': h.facilities, 'highlights': h.highlights,
                'matching': h.matching, 'floor': h.floor, 'build_year': h.build_year,
                'decoration': h.decoration, 'house_type': h.house_type,
                'description': h.description, 'image_url': h.image_url,
                'longitude': h.longitude, 'latitude': h.latitude,
                'contact_name': h.contact_name, 'contact_phone': h.contact_phone,
                'publish_date': h.publish_date,
            })
    total_pages = (total + page_size - 1) // page_size if page_size > 0 else 1
    return {"houses": house_dicts, "total": total, "page": page, "page_size": page_size, "total_pages": total_pages}


@router.get("/hot", summary="热门房源")
async def get_hot_houses(
    limit: int = Query(6, ge=1, le=20),
    db: Session = Depends(get_db),
):
    """获取热门房源（按浏览量排序）"""
    service = HouseService(db)
    houses = service.get_hot_houses(limit=limit)
    return {"houses": houses}


@router.get("/new", summary="最新房源")
async def get_new_houses(
    limit: int = Query(6, ge=1, le=20),
    db: Session = Depends(get_db),
):
    """获取最新发布房源"""
    service = HouseService(db)
    houses = service.get_new_houses(limit=limit)
    return {"houses": houses}


# ===== 智能推荐 =====
@router.get("/recommendations", tags=["智能推荐"])
async def get_recommendations(
    user_id: int = 0, limit: int = 10,
    db: Session = Depends(get_db),
):
    """智能推荐房源"""
    service = HouseService(db)
    recs, total = service.get_recommendations(user_id, limit)
    return {"recommendations": recs, "total": total}


# ===== 房价估算 =====
@router.post("/estimate-price", tags=["房价估算"])
async def estimate_price(
    data: dict,
    db: Session = Depends(get_db),
):
    """房价智能估算（总价）

    注意：area 来自前端输入框，可能为空串或非法字符，
    这里统一兜底为 80㎡，避免 float("") 直接抛 500。
    """
    try:
        area = float(data.get("area") or 80)
    except (TypeError, ValueError):
        area = 80.0
    service = HouseService(db)
    result = service.estimate_price(
        rooms=data.get("rooms") or "2室1厅",
        area=area,
        region=data.get("region") or "朝阳区",
        direction=data.get("direction") or "南北",
        decoration=data.get("decoration") or "精装修",
        floor=data.get("floor") or "中层",
    )
    return result


# ===== 收藏功能 =====
# ===== 收藏功能（行为归属由 Token 决定，前端不再传 user_id） =====
@router.get("/favorites", tags=["房源收藏"])
async def get_favorites(
    page: int = 1, page_size: int = 20,
    payload: dict = Depends(require_user),
    db: Session = Depends(get_db),
):
    """获取当前登录用户的收藏列表"""
    service = HouseService(db)
    favs, total = service.get_favorites(int(payload["sub"]), page, page_size)
    return {"favorites": favs, "total": total}


@router.post("/favorites", tags=["房源收藏"])
async def add_favorite(
    data: dict,
    payload: dict = Depends(require_user),
    db: Session = Depends(get_db),
):
    """收藏房源：user_id 取自 Token，保证行为数据归属当前用户"""
    service = HouseService(db)
    ok = service.add_favorite(int(payload["sub"]), data.get("house_id", 0))
    return {"success": ok, "message": "收藏成功" if ok else "收藏失败"}


@router.delete("/favorites/{house_id}", tags=["房源收藏"])
async def remove_favorite(
    house_id: int,
    payload: dict = Depends(require_user),
    db: Session = Depends(get_db),
):
    """取消收藏"""
    service = HouseService(db)
    ok = service.remove_favorite(int(payload["sub"]), house_id)
    return {"success": ok, "message": "已取消收藏" if ok else "操作失败"}


@router.get("/favorites/check/{house_id}", tags=["房源收藏"])
async def check_favorite(
    house_id: int,
    payload: dict = Depends(require_user),
    db: Session = Depends(get_db),
):
    """检查当前用户是否已收藏该房源"""
    service = HouseService(db)
    favorited = service.is_favorited(int(payload["sub"]), house_id)
    return {"favorited": favorited}


# ===== 对比功能（对比表按用户名关联，用户名取自 Token） =====
@router.get("/compare", tags=["房源对比"])
async def get_compare(
    payload: dict = Depends(require_login),
    db: Session = Depends(get_db),
):
    """获取当前登录用户的对比列表（含单价/月供/相对区域均价与一句话结论）"""
    service = HouseService(db)
    items, total = service.get_compare_list(payload.get("name") or "")
    return {
        "items": items,
        "total": total,
        "verdict": service.get_compare_verdict(items),
    }


@router.post("/compare", tags=["房源对比"])
async def add_compare(
    data: dict,
    payload: dict = Depends(require_login),
    db: Session = Depends(get_db),
):
    """添加到对比"""
    service = HouseService(db)
    ok = service.add_compare(payload.get("name") or "", data.get("house_id", 0))
    return {"success": ok, "message": "已添加到对比" if ok else "添加失败（最多4套）"}


@router.delete("/compare/{house_id}", tags=["房源对比"])
async def remove_compare(
    house_id: int,
    payload: dict = Depends(require_login),
    db: Session = Depends(get_db),
):
    """从对比移除"""
    service = HouseService(db)
    ok = service.remove_compare(payload.get("name") or "", house_id)
    return {"success": ok, "message": "已移除" if ok else "操作失败"}


@router.delete("/compare", tags=["房源对比"])
async def clear_compare(
    payload: dict = Depends(require_login),
    db: Session = Depends(get_db),
):
    """清空对比"""
    service = HouseService(db)
    ok = service.clear_compare(payload.get("name") or "")
    return {"success": ok, "message": "已清空对比"}


# ===== 浏览历史 =====
@router.get("/browse-history", tags=["浏览历史"])
async def get_browse_history(
    page: int = 1, page_size: int = 20,
    payload: dict = Depends(require_user),
    db: Session = Depends(get_db),
):
    """获取当前登录用户的浏览历史"""
    service = HouseService(db)
    history, total = service.get_browse_history(int(payload["sub"]), page, page_size)
    return {"history": history, "total": total}


@router.delete("/browse-history/{house_id}", tags=["浏览历史"])
async def remove_browse_history(
    house_id: int,
    payload: dict = Depends(require_user),
    db: Session = Depends(get_db),
):
    """删除单条浏览记录"""
    service = HouseService(db)
    ok = service.remove_browse_history(int(payload["sub"]), house_id)
    return {"success": ok, "message": "已删除" if ok else "记录不存在"}


@router.delete("/browse-history", tags=["浏览历史"])
async def clear_browse_history(
    payload: dict = Depends(require_user),
    db: Session = Depends(get_db),
):
    """清空当前登录用户的浏览历史"""
    service = HouseService(db)
    ok = service.clear_browse_history(int(payload["sub"]))
    return {"success": ok, "message": "已清空"}



# ===== 地图找房 =====
@router.get("/map/all", tags=["地图找房"])
async def get_all_houses_for_map(
    region: Optional[str] = None,
    limit: int = 800,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """获取房源的地图标记数据（用于地图找房，默认返回800条采样数据）"""
    service = HouseService(db)
    houses, total = service.get_all_houses_for_map(region, limit, offset)
    return {"houses": houses, "total": total}


@router.get("/regions", tags=["地图找房"])
async def get_regions(db: Session = Depends(get_db)):
    """全部区域及房源数（下拉框用，避免前端写死区域列表）"""
    return {"regions": HouseService(db).list_regions()}


@router.get("/stats", tags=["房源"])
async def get_house_stats(db: Session = Depends(get_db)):
    """首页概览统计（全量口径）：总数 / 覆盖行政区数 / 平均总价（万）"""
    return HouseService(db).get_overview_stats()

# ===== 详情页「周边配套」：高德周边搜索 =====
@router.get("/{house_id}/surroundings", tags=["房源"])
async def get_house_surroundings(
    house_id: int,
    radius: int = Query(1500, ge=200, le=5000),
    db: Session = Depends(get_db),
):
    """房源周边设施：交通 / 学校 / 餐饮 / 购物 / 医院 / 银行，含距离。

    外部依赖失败时返回空分类（详情页照常展示，只是没有配套列表）。
    """
    house = HouseService(db).get_house_by_id(house_id)
    if not house:
        raise HTTPException(status_code=404, detail="房源不存在")
    if not house.longitude or not house.latitude:
        return {"house_id": house_id, "categories": [], "center": None}

    geo = get_external_geo_service()
    categories = (
        geo.surroundings(house.longitude, house.latitude, radius) if geo.enabled else []
    )
    return {
        "house_id": house_id,
        "center": {"lng": float(house.longitude), "lat": float(house.latitude)},
        "address": house.address,
        "radius": radius,
        "categories": categories,
    }


# ===== 房源详情（必须在所有 /houses/xxx 固定路径之后） =====
@router.get("/{house_id}", summary="房源详情")
async def get_house_detail(
    house_id: int, user_id: int = 0,
    payload: Optional[dict] = Depends(optional_user),
    db: Session = Depends(get_db),
):
    """获取房源详情：带普通用户 Token 时自动记录浏览足迹

    这里不强制 response_model：库中存在 created_at/updated_at 为 datetime 的历史数据，
    严格校验会抛 ResponseValidationError（500），改用 jsonable_encoder 自动序列化。
    """
    service = HouseService(db)
    house = service.get_house_by_id(house_id)
    if not house:
        raise HTTPException(status_code=404, detail="房源不存在")
    service.increment_views(house_id)
    # 优先使用 Token 中的用户 id（防止前端伪造 user_id），未登录时兼容旧参数
    uid = int(payload["sub"]) if payload and payload.get("role") == "user" else user_id
    if uid and uid > 0:
        service.add_browse_history(uid, house_id)
        # 记录足迹内部会 commit，ORM 对象属性随之过期；Pydantic 序列化只读 __dict__，
        # 不刷新就会返回 {}（消息通知点进来、刷新页面时表现为「详情页全空」）
        db.refresh(house)
    return house


# ===== 评论功能 =====
@router.get("/{house_id}/comments", tags=["房源评论"])
async def get_comments(
    house_id: int, page: int = 1, page_size: int = 20,
    db: Session = Depends(get_db),
):
    """获取房源评论（每条评论附带其回复列表 replies）"""
    service = HouseService(db)
    comments, total, avg_score = service.get_comments_with_stats(house_id, page, page_size)
    # 历史评论可能没打情感标签：本页顺带补上，详情页评论可以显示情感徽章
    if SentimentService(db).tag_comments(comments):
        db.commit()
    replies_map = CommentService(db).get_replies_map([c.id for c in comments])
    return {
        "comments": [
            {
                "id": c.id,
                "house_id": c.house_id,
                "user_id": c.user_id,
                "user_name": c.user_name,
                "score": c.score,
                "content": c.content,
                "sentiment": c.sentiment,
                "create_time": c.create_time.isoformat() if c.create_time else None,
                "replies": replies_map.get(int(c.id), []),
            }
            for c in comments
        ],
        "total": total,
        "avg_score": avg_score,
    }


@router.post("/{house_id}/comments/{comment_id}/replies", tags=["房源评论"])
async def add_comment_reply(
    house_id: int, comment_id: int, data: dict,
    payload: dict = Depends(require_user),
    db: Session = Depends(get_db),
):
    """回复某条评论：登录用户可回复，reply_to 记录被回复人昵称

    传 reply_to_id 表示回复「楼中楼里的某条回复」，此时会把消息发给那条回复的作者；
    否则消息发给评论作者。自己回复自己不产生消息。
    """
    content = (data.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="回复内容不能为空")
    if len(content) > 300:
        raise HTTPException(status_code=400, detail="回复内容不能超过 300 字")

    service = CommentService(db)
    comment = service.get_comment(comment_id)
    if not comment or int(comment.house_id or 0) != int(house_id):
        raise HTTPException(status_code=404, detail="评论不存在")

    # 被回复对象：优先按 reply_to_id 精确定位（拿到 user_id 才能发消息）
    target_reply = None
    raw_reply_id = data.get("reply_to_id")
    if raw_reply_id:
        try:
            target_reply = service.get_reply(int(raw_reply_id))
        except (TypeError, ValueError):
            target_reply = None
        if target_reply and int(target_reply.comment_id or 0) != int(comment_id):
            target_reply = None

    reply_to = (target_reply.user_name if target_reply else data.get("reply_to") or "").strip()[:50]
    reply = service.add_reply(
        comment_id=comment_id,
        house_id=house_id,
        user_id=int(payload["sub"]),
        user_name=payload.get("name") or "匿名用户",
        content=content,
        reply_to=reply_to,
    )

    # ---- 站内消息：通知被回复的人 ----
    replier_id = int(payload["sub"])
    replier_name = payload.get("name") or "匿名用户"
    notify_user_id = target_reply.user_id if target_reply else comment.user_id
    house_title = db.query(HouseInfo.title).filter(HouseInfo.id == house_id).scalar() or ""
    title = (f"{replier_name} 回复了你的回复"
             if target_reply else f"{replier_name} 回复了你的评价")
    NotificationService(db).create(
        user_id=notify_user_id,
        title=title,
        content=content[:200],
        type="comment_reply",
        house_id=house_id,
        house_title=house_title,
        comment_id=comment_id,
        from_user_id=replier_id,
        from_user_name=replier_name,
    )
    return {"success": True, "reply_id": reply.id, "message": "回复成功"}


@router.post("/{house_id}/comments", tags=["房源评论"])
async def add_comment(
    house_id: int, data: dict,
    payload: dict = Depends(require_user),
    db: Session = Depends(get_db),
):
    """发表评论：评论人从 Token 解析，内容不能为空，评分 1~5"""
    content = (data.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="评论内容不能为空")
    if len(content) > 500:
        raise HTTPException(status_code=400, detail="评论内容不能超过 500 字")

    score = data.get("score", 5)
    try:
        score = float(score)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="评分格式不正确")
    score = min(max(score, 1.0), 5.0)

    service = HouseService(db)
    comment = service.add_comment(
        house_id=house_id,
        user_id=int(payload["sub"]),
        user_name=payload.get("name") or "匿名用户",
        score=score,
        content=content,
    )
    return {"success": True, "comment_id": comment.id, "message": "评论成功"}


@router.delete("/{house_id}/comments/{comment_id}", tags=["房源评论"])
async def delete_my_comment(
    house_id: int, comment_id: int,
    payload: dict = Depends(require_user),
    db: Session = Depends(get_db),
):
    """删除自己的评价：只能删本人发表的，管理员批量清理走后台接口

    删评论会连带删掉楼中楼的回复，避免留下孤儿数据。
    """
    service = CommentService(db)
    comment = service.get_comment(comment_id)
    if not comment or int(comment.house_id or 0) != int(house_id):
        raise HTTPException(status_code=404, detail="评论不存在")
    if int(comment.user_id or 0) != int(payload["sub"]):
        raise HTTPException(status_code=403, detail="只能删除自己发表的评价")

    replies = service.delete_replies_of([comment_id])
    db.delete(comment)
    db.commit()
    return {"success": True, "deleted_replies": replies, "message": "评价已删除"}


# ===== 房源增删改（管理后台，写操作后同步向量库） =====
@router.post("", status_code=status.HTTP_201_CREATED, summary="新增房源")
async def create_house(
    payload: HouseCreateRequest,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_permission("house:create")),
):
    """新增房源，并立即同步写入向量库（需 house:create 权限）"""
    service = HouseService(db)
    try:
        house = service.create_house(payload.model_dump(exclude_none=True))
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"新增房源失败: {e}")

    synced = IndexSyncService(db).sync_house(house)
    return {
        "message": "房源创建成功",
        "house": house.to_dict(),
        "vector_synced": synced,
    }


@router.put("/{house_id}", summary="更新房源")
async def update_house(
    house_id: int,
    payload: HouseUpdateRequest,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_permission("house:update")),
):
    """更新房源，并重建该房源对应的向量（需 house:update 权限）"""
    service = HouseService(db)
    house = service.update_house(house_id, payload.model_dump(exclude_none=True))
    if not house:
        raise HTTPException(status_code=404, detail="房源不存在")

    synced = IndexSyncService(db).sync_house(house)
    return {
        "message": "房源更新成功",
        "house": house.to_dict(),
        "vector_synced": synced,
    }


@router.delete("/{house_id}", summary="删除房源")
async def delete_house(
    house_id: int,
    db: Session = Depends(get_db),
    _admin: dict = Depends(require_permission("house:delete")),
):
    """删除房源，并清理其对应的向量（需 house:delete 权限）"""
    service = HouseService(db)
    if not service.delete_house(house_id):
        raise HTTPException(status_code=404, detail="房源不存在")

    sync_service = IndexSyncService(db)
    removed = sync_service.remove_house_vectors(house_id)
    sync_service.save_version()
    return {
        "message": "房源已删除",
        "house_id": house_id,
        "vectors_removed": removed,
    }
