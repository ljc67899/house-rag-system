# -*- coding: utf-8 -*-
"""知识库文档元信息模型

向量库里存的是「切片」（metadata.type=document），本表存的是「文档级」信息：
文件名、大小、切片数、上传人等。两边用 doc_id 关联：

    knowledge_document.doc_id  ==  vector metadata.doc_id

为什么还要单独建表：
    向量库只能告诉我们「有哪些切片」，拿不到文件大小、上传时间这些管理页需要的字段；
    而切片数又是向量库最准确（删过就等于真实可检索数量），所以列表以向量库为准、
    本表只做补充，避免两边对不上。
"""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class KnowledgeDocument(SQLModel, table=True):
    """知识库文档（管理页列表的数据来源之一）"""

    __tablename__ = "knowledge_document"

    id: Optional[int] = Field(default=None, primary_key=True)
    doc_id: Optional[str] = Field(
        default=None, max_length=64, unique=True, index=True, description="文档唯一标识（与向量 metadata 一致）"
    )
    filename: Optional[str] = Field(default=None, max_length=255, description="原始文件名")
    file_type: Optional[str] = Field(default=None, max_length=20, description="解析类型：pdf/docx/text")
    file_size: int = Field(default=0, description="文件大小（字节）")
    char_count: int = Field(default=0, description="解析出的正文字符数")
    chunk_count: int = Field(default=0, description="上传时写入的切片数")
    file_path: Optional[str] = Field(default=None, max_length=512, description="落盘路径（删除时一并清理）")
    status: Optional[str] = Field(default="completed", max_length=20, description="索引状态")
    uploader: Optional[str] = Field(default=None, max_length=64, description="上传人")
    create_time: Optional[datetime] = Field(default_factory=datetime.now, description="上传时间")


__all__ = ["KnowledgeDocument"]
