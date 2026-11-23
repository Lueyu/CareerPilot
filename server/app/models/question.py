"""题库 / 面经导入流水线。

题目来源 PRD: PUBLIC_LICENSED | USER_PRIVATE | GENERATED | VERIFIED_PUBLIC。
去重策略: 保留原题(canonical) + variants; 合并时被合并方归入 canonical 的变体列表。
"""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

SOURCE_PUBLIC = "PUBLIC_LICENSED"
SOURCE_PRIVATE = "USER_PRIVATE"
SOURCE_GENERATED = "GENERATED"
SOURCE_VERIFIED = "VERIFIED_PUBLIC"
STUDY_CHAT_REVIEW_META = {"assessment_use": "PRACTICE_ONLY", "origin": "study_chat"}


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    short_code: Mapped[str] = mapped_column(String(32), unique=True)  # Q-018 / eval-003
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True)
    # NULL = 公共题库(所有工作空间可见); 非空 = 私有
    title: Mapped[str] = mapped_column(Text)
    # mcq | open | coding | behavioral
    qtype: Mapped[str] = mapped_column(String(24), default="open")
    topic: Mapped[str] = mapped_column(String(64))          # 原 8 主题 code (rag/harness/...)
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id"), nullable=True)  # 映射到能力域
    difficulty: Mapped[int] = mapped_column(Integer, default=3)  # 1-5
    options: Mapped[list | None] = mapped_column(JSONB, nullable=True)     # MCQ 选项
    answer_html: Mapped[str | None] = mapped_column(Text, nullable=True)   # 答案解析(公开题)
    # 评分要点: {points: [...], rubric: [...]} — Agent Evaluation 依据
    rubric: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    company: Mapped[str | None] = mapped_column(String(160), nullable=True)
    role: Mapped[str | None] = mapped_column(String(120), nullable=True)
    level: Mapped[str | None] = mapped_column(String(32), nullable=True)   # P5/P6...
    occurred_on: Mapped[date | None] = mapped_column(Date, nullable=True)  # 面试日期
    source_type: Mapped[str] = mapped_column(String(32), default=SOURCE_PUBLIC)
    tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    keywords: Mapped[str | None] = mapped_column(JSONB, nullable=True)
    generation_meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # canonical_question_id 非空 => 本题是某题的变体(重复问法)
    canonical_question_id: Mapped[int | None] = mapped_column(ForeignKey("questions.id"), nullable=True)
    is_demo: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    skill = relationship("Skill", lazy="joined")

    @property
    def is_study_chat_review(self) -> bool:
        meta = self.generation_meta or {}
        return self.workspace_id is not None and all(meta.get(key) == value
                                                    for key, value in STUDY_CHAT_REVIEW_META.items())


# 自引用: canonical_question_id 非空的题是变体; variants 列出挂在它下面的重复问法
Question.canonical = relationship("Question", remote_side=[Question.id], back_populates="variants", lazy="selectin")
Question.variants = relationship("Question", back_populates="canonical", lazy="selectin")


class ImportSession(Base):
    """面经导入会话: Upload→Parse→Extract→SkillMap→Dedup→Draft→Review→Approve。"""

    __tablename__ = "import_sessions"

    STATUS_UPLOADED = "UPLOADED"
    STATUS_PARSING = "PARSING"
    STATUS_REVIEW = "REVIEW"
    STATUS_APPROVED = "APPROVED"
    STATUS_LATER = "LATER"   # 稍后处理

    id: Mapped[int] = mapped_column(primary_key=True)
    short_code: Mapped[str] = mapped_column(String(32), unique=True)  # IM-103
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    paste_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default=STATUS_UPLOADED)
    meta: Mapped[list | None] = mapped_column(JSONB, nullable=True)   # 公司/职级/日期等提取结果
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list["ImportItem"]] = relationship(back_populates="session", lazy="selectin", cascade="all, delete-orphan")


class ImportItem(Base):
    """导入会话中提取的单条题目草稿(带置信度), 审核通过后转入题库。"""

    __tablename__ = "import_items"

    STATUS_DRAFT = "DRAFT"
    STATUS_APPROVED = "APPROVED"
    STATUS_REJECTED = "REJECTED"
    STATUS_MERGED = "MERGED"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("import_sessions.id", ondelete="CASCADE"))
    question_id: Mapped[int | None] = mapped_column(ForeignKey("questions.id"), nullable=True)  # 通过后生成
    merged_into: Mapped[int | None] = mapped_column(ForeignKey("questions.id"), nullable=True)  # 合并去重目标
    title: Mapped[str] = mapped_column(Text)
    extract_confidence: Mapped[float] = mapped_column(Float, default=0.9)
    suggested_skill_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    suggested_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    draft_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_rubric: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    generation_meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_duplicate_of: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 疑似重复的 question.id
    status: Mapped[str] = mapped_column(String(24), default=STATUS_DRAFT)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped[ImportSession] = relationship(back_populates="items")
