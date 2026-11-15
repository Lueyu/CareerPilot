"""考试与作答。

状态机(PRD): CREATED→ACTIVE→SUBMITTED→TRANSCRIPT_CONFIRMED→FROZEN→SCORING→COMPLETED。
  - SUBMITTED 后答卷只读, 进入誊写确认(TRANSCRIPT_CONFIRMED);
  - FROZEN: 题目+答案+rubric 全部锁定, 评分前不可变;
  - SCORING: 评分中(异步);
  - COMPLETED: 出报告, 写证据, 更新 SkillState。
考试类型: diagnosis(独立诊断) | focused(专项) | bank_exam(面经考试) | project(项目面试)。
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Exam(Base):
    __tablename__ = "exams"

    STATUS_CREATED = "CREATED"
    STATUS_ACTIVE = "ACTIVE"
    STATUS_SUBMITTED = "SUBMITTED"
    STATUS_TRANSCRIPT_CONFIRMED = "TRANSCRIPT_CONFIRMED"
    STATUS_FROZEN = "FROZEN"
    STATUS_SCORING = "SCORING"
    STATUS_COMPLETED = "COMPLETED"

    KIND_DIAGNOSIS = "diagnosis"
    KIND_FOCUSED = "focused"
    KIND_BANK = "bank_exam"
    KIND_PROJECT = "project"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default=STATUS_CREATED)
    # 选题策略快照: 为什么选这些题(可解释性)
    selection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)     # 0-100
    total_points: Mapped[float] = mapped_column(Float, default=0)
    gained_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)    # 报告小结
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    questions: Mapped[list["ExamQuestion"]] = relationship(
        back_populates="exam", lazy="selectin", cascade="all, delete-orphan", order_by="ExamQuestion.order_no"
    )


class ExamQuestion(Base):
    __tablename__ = "exam_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id", ondelete="CASCADE"))
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"))
    order_no: Mapped[int] = mapped_column(Integer)
    # 冻结快照(FROZEN 时写入): 题目+选项+rubric 的不可变副本
    frozen_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    exam: Mapped[Exam] = relationship(back_populates="questions")
    question = relationship("Question", lazy="joined")
    answer: Mapped["ExamAnswer | None"] = relationship(back_populates="exam_question", lazy="selectin", cascade="all, delete-orphan", uselist=False)


class ExamAnswer(Base):
    __tablename__ = "exam_answers"

    id: Mapped[int] = mapped_column(primary_key=True)
    exam_question_id: Mapped[int] = mapped_column(ForeignKey("exam_questions.id", ondelete="CASCADE"))
    # MCQ: 选项索引; open: 文本
    choice_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 语音转写原文(如使用语音输入)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    points: Mapped[float | None] = mapped_column(Float, nullable=True)       # 得分
    max_points: Mapped[float] = mapped_column(Float, default=5.0)
    verdict: Mapped[str | None] = mapped_column(String(24), nullable=True)   # CORRECT | PARTIAL | WRONG
    feedback: Mapped[str | None] = mapped_column(JSONB, nullable=True)       # 逐要点点评+建议
    evidence_refs: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    exam_question: Mapped[ExamQuestion] = relationship(back_populates="answer")
