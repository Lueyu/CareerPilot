"""Project Intelligence 模型: GitHub 项目 / 扫描运行 / 发现(=证据来源) / 职业资产 / 资产更新运行。

设计要点(任务书 Engine 6):
- 只读接入 GitHub, 不执行用户仓库代码;
- 仓库存在某项功能 ≠ 用户个人掌握, finding 用证据等级区分;
- 资产更新运行最终产出 PublicContentDraft, 复用公开内容发布管线(隐私/许可/审核/PR/回滚)。
"""
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Project(Base):
    """用户授权分析的 GitHub 仓库。"""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    repo_url: Mapped[str] = mapped_column(Text)              # https://github.com/owner/repo
    owner: Mapped[str] = mapped_column(String(255))
    repo: Mapped[str] = mapped_column(String(255))
    branch: Mapped[str] = mapped_column(String(128), default="main")
    # 最近扫描状态: NEVER / SUCCESS / FAILED
    status: Mapped[str] = mapped_column(String(16), default="NEVER")
    last_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # description/languages/size 等
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    scans = relationship("ProjectScan", back_populates="project", cascade="all, delete-orphan", order_by="ProjectScan.id.desc()")
    findings = relationship("ProjectFinding", back_populates="project", cascade="all, delete-orphan")


class ProjectScan(Base):
    """一次仓库扫描运行(带 Trace 关联, 失败真实落库不假装成功)。"""

    __tablename__ = "project_scans"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    # RUNNING / SUCCESS / FAILED
    status: Mapped[str] = mapped_column(String(16), default="RUNNING")
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSONB, nullable=True)   # {files, languages, findings_count, verified}
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[int | None] = mapped_column(ForeignKey("traces.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project = relationship("Project", back_populates="scans")
    findings = relationship("ProjectFinding", back_populates="scan", cascade="all, delete-orphan")


class ProjectFinding(Base):
    """扫描发现: 引用文件路径/SHA 的能力域证据。level 采用证据等级体系。"""

    __tablename__ = "project_findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("project_scans.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("skills.id"), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 能力域 key(冗余展示)
    # VERIFIED / HIGH / UNVERIFIED_CLAIM
    level: Mapped[str] = mapped_column(String(24), default="UNVERIFIED_CLAIM")
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    evidence_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)  # EV-xxx(入库 evidence 后回填)
    evidence_id: Mapped[int | None] = mapped_column(ForeignKey("evidence.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    scan = relationship("ProjectScan", back_populates="findings")
    project = relationship("Project", back_populates="findings")


class CareerAsset(Base):
    """职业资产: 简历 / 个人网站页面(公开展示物的登记与指纹)。"""

    __tablename__ = "career_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24), default="WEBSITE_PAGE")  # RESUME / WEBSITE_PAGE
    name: Mapped[str] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)    # 线上地址
    content_path: Mapped[str | None] = mapped_column(String(512), nullable=True)  # 内容仓库内路径(portal/content/...)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)   # 最近同步内容指纹
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssetUpdateRun(Base):
    """职业资产更新流水线运行: 项目变更 → diff → 风险分级 → 复用公开内容管线发布。"""

    __tablename__ = "asset_update_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    short_code: Mapped[str] = mapped_column(String(32), unique=True)  # AU-xxxx
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    # PENDING_DIFF / DRAFT_CREATED / PUBLISHED / FAILED / ROLLED_BACK
    status: Mapped[str] = mapped_column(String(24), default="PENDING_DIFF")
    diff_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    # LOW / MEDIUM / HIGH(高风险必须人工审核)
    risk_level: Mapped[str] = mapped_column(String(16), default="MEDIUM")
    risk_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_id: Mapped[int | None] = mapped_column(ForeignKey("public_content_drafts.id", ondelete="SET NULL"), nullable=True)
    publication_id: Mapped[int | None] = mapped_column(ForeignKey("content_publication_runs.id", ondelete="SET NULL"), nullable=True)
    trace_id: Mapped[int | None] = mapped_column(ForeignKey("traces.id", ondelete="SET NULL"), nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
