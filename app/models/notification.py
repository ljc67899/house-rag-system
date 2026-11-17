# -*- coding: utf-8 -*-
"""站内消息（通知）模型

当前只承载一种业务：**评论/回复被别人回复**。
设计成通用表（type 字段），后续要加「收藏降价提醒」「系统公告」时不用改表结构。

为什么不直接在评论表上加标记：
    同一条评论可能被多个人回复，接收人各不相同，必须按「接收人」维度单独存一行；
    已读状态也是每人一份，所以独立成表最自然。
"""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, Index, SQLModel


class UserNotification(SQLModel, table=True):
    """用户站内消息"""

    __tablename__ = "user_notification"
    __table_args__ = (
        # 未读角标查询走 (user_id, is_read)，避免每次轮询全表扫
        Index("ix_notify_user_read", "user_id", "is_read"),
        {"comment": "用户站内消息（评论被回复等）"},
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, index=True, description="接收人ID")
    type: Optional[str] = Field(default="comment_reply", max_length=32, description="消息类型")
    title: Optional[str] = Field(default=None, max_length=100, description="消息标题")
    content: Optional[str] = Field(default=None, max_length=500, description="消息正文（回复内容摘要）")
    house_id: Optional[int] = Field(default=None, description="关联房源ID")
    house_title: Optional[str] = Field(default=None, max_length=255, description="房源标题快照")
    comment_id: Optional[int] = Field(default=None, description="关联评论ID")
    from_user_id: Optional[int] = Field(default=None, description="触发人ID")
    from_user_name: Optional[str] = Field(default=None, max_length=50, description="触发人昵称")
    is_read: bool = Field(default=False, description="是否已读")
    create_time: Optional[datetime] = Field(default_factory=datetime.now, description="消息时间")
