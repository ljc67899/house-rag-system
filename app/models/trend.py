# -*- coding: utf-8 -*-
"""房价走势 SQLModel 模型"""
from typing import Optional

from sqlalchemy import Index
from sqlmodel import Field, SQLModel


class HousePriceTrend(SQLModel, table=True):
    """区域月度挂牌均价序列"""
    __tablename__ = "house_price_trend"
    __table_args__ = (
        Index("ix_trend_region_month", "region", "month", unique=True),
        {"comment": "区域月度挂牌均价"},
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    region: Optional[str] = Field(default=None, max_length=50, index=True, nullable=False, description="区域")
    month: Optional[str] = Field(default=None, max_length=20, index=True, nullable=False, description="月份，如 2020-01")
    avg_price: Optional[float] = Field(default=None, nullable=False, description="该区域当月挂牌均价（万元/套）")
    count: Optional[int] = Field(default=0, description="当月样本量")
    source: Optional[str] = Field(default="simulated", max_length=50, description="数据来源：real / simulated")
