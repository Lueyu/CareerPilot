"""工作空间与用户。

CareerPilot 是多租户系统: 所有业务数据都挂在 workspace 下,
API 层通过依赖注入强制 workspace 过滤(应用层隔离; 生产可叠加 PostgreSQL RLS)。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspaces: Mapped[list["Workspace"]] = relationship(
        secondary="workspace_members", back_populates="members", lazy="selectin"
    )


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    # 目标岗位: AI Agent 应用开发工程师 · P6(阿里 P6 对标)
    target_role: Mapped[str] = mapped_column(String(160), default="AI Agent 应用开发工程师")
    target_level: Mapped[str] = mapped_column(String(16), default="P6")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    # 工作空间级偏好(JSONB): 自动化规则默认值/界面偏好等, 结构由设置页演进
    preferences: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    members: Mapped[list["User"]] = relationship(
        secondary="workspace_members", back_populates="workspaces", lazy="selectin"
    )


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"

    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), default="owner")  # owner | admin | member
