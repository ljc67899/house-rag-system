# -*- coding: utf-8 -*-
"""文档解析服务"""

import os
from typing import Tuple
from loguru import logger
from app.config import settings
from app.core.exceptions import RAGException


class DocumentService:
    TEXT_EXTENSIONS = {".txt", ".md"}
    DOC_EXTENSIONS = {".docx"}
    PDF_EXTENSIONS = {".pdf"}

    def parse(self, file_path: str) -> Tuple[str, str]:
        ext = os.path.splitext(file_path)[1].lower()
        if ext in self.TEXT_EXTENSIONS:
            return self._parse_text(file_path), "text"
        elif ext in self.DOC_EXTENSIONS:
            return self._parse_docx(file_path), "docx"
        elif ext in self.PDF_EXTENSIONS:
            return self._parse_pdf(file_path), "pdf"
        else:
            raise RAGException(f"不支持的文件类型: {ext}")

    def _parse_text(self, file_path: str) -> str:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        except UnicodeDecodeError:
            with open(file_path, "r", encoding="gbk") as f:
                return f.read()

    def _parse_docx(self, file_path: str) -> str:
        try:
            from docx import Document
            doc = Document(file_path)
            paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
            return "\n".join(paragraphs)
        except Exception as e:
            logger.error(f"DOCX parse error: {e}")
            raise RAGException(f"Word文档解析失败: {e}")

    def _parse_pdf(self, file_path: str) -> str:
        try:
            import pdfplumber
            texts = []
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()
                    if text and text.strip():
                        texts.append(text)
            return "\n\n".join(texts)
        except Exception as e:
            logger.error(f"PDF parse error: {e}")
            raise RAGException(f"PDF文档解析失败: {e}")

    def is_supported(self, file_path: str) -> bool:
        ext = os.path.splitext(file_path)[1].lower()
        return ext in (self.TEXT_EXTENSIONS | self.DOC_EXTENSIONS | self.PDF_EXTENSIONS)


_document_service = None

def get_document_service() -> DocumentService:
    global _document_service
    if _document_service is None:
        _document_service = DocumentService()
    return _document_service
