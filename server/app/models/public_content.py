"""公开内容发布: 草稿 / 来源 / 发布运行 / 规则 / 审计。

这是本地私人数据系统(CareerPilot)与公开静态网站(agent-interview.icu)之间
唯一的、显式的、可审计的边界。任何内容要进入公开站, 必须走:
草稿 → 校验(隐私/事实/许可) → 人工审核 → 发布运行(建分支/写文件/开 PR) →
构建追踪 → 发布; 回滚写入上一个可用版本。

红线(Part E):
- 本地考试/记忆/简历/能力画像数据绝不直接进入公开站;
- 只有用户明确授权的低风险内容才自动 merge PR;
- 每个发布运行都有幂等键 + Trace + 审计记录。
"""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


# --- 草稿状态机(C2) ---
DRAFT_STATUS_DRAFT = "DRAFT"
DRAFT_STATUS_VALIDATING = "VALIDATING"
DRAFT_STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"
DRAFT_STATUS_APPROVED = "APPROVED"
DRAFT_STATUS_PR_CREATED = "PR_CREATED"
DRAFT_STATUS_BUILDING = "BUILDING"
DRAFT_STATUS_PUBLISHED = "PUBLISHED"
DRAFT_STATUS_VALIDATION_FAILED = "VALIDATION_FAILED"
DRAFT_STATUS_BUILD_FAILED = "BUILD_FAILED"
DRAFT_STATUS_REJECTED = "REJECTED"
DRAFT_STATUS_ROLLED_BACK = "ROLLED_BACK"


class PublicContentSource(Base):
    """公开内容引用的来源(URL / 题库题 / 导入文档), 承载许可与事实检查依据。"""

    __tablename__ = "public_content_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    draft_id: Mapped[int | None] = mapped_column(ForeignKey("public_content_drafts.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), default="EXTERNAL_URL")  # QUESTION_BANK / EXTERNAL_URL / IMPORTED_DOC
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 许可状态: PUBLIC_DOMAIN / PERMISSIVE / UNKNOWN / RESTRICTED / USER_OWNED
    license_status: Mapped[str] = mapped_column(String(24), default="UNKNOWN")
    license_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 隐私扫描: PASS / FAIL / PENDING; 事实检查: PASS / WARN / FAIL / PENDING
    privacy_scan: Mapped[str] = mapped_column(String(16), default="PENDING")
    fact_check: Mapped[str] = mapped_column(String(16), default="PENDING")
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PublicContentDraft(Base):
    """公开内容草稿: Markdown + 结构化 JSON + 校验结果 + 审核状态。"""

    __tablename__ = "public_content_drafts"
    __table_args__ = (UniqueConstraint("workspace_id", "slug", name="uq_draft_ws_slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    short_code: Mapped[str] = mapped_column(String(32), unique=True)  # CT-xxxx
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(Text)
    slug: Mapped[str] = mapped_column(String(255))            # 站内路径 slug, 工作空间内唯一
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    markdown_body: Mapped[str] = mapped_column(Text, default="")
    json_meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # 结构化元数据/前后置字段

    # 校验结果(由 content_validation 服务写入)
    privacy_scan_status: Mapped[str] = mapped_column(String(16), default="PENDING")   # PASS/FAIL
    fact_check_status: Mapped[str] = mapped_column(String(16), default="PENDING")     # PASS/WARN/FAIL
    license_status: Mapped[str] = mapped_column(String(24), default="UNKNOWN")
    validation_errors: Mapped[list | None] = mapped_column(JSONB, nullable=True)      # [{check, level, message}]

    # 发布目标(默认取 settings, 允许单草稿覆盖)
    target_repo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_path: Mapped[str | None] = mapped_column(Text, nullable=True)              # 相对 content_dir 的文件路径

    # 审核
    status: Mapped[str] = mapped_column(String(32), default=DRAFT_STATUS_DRAFT)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    sources = relationship("PublicContentSource", backref="draft", lazy="selectin", cascade="all, delete-orphan")
    publications = relationship("ContentPublication", back_populates="draft", lazy="selectin")


class ContentPublication(Base):
    """一次发布运行: 幂等键 + 目标分支/SHA + PR + 构建追踪 + 回滚信息。"""

    __tablename__ = "content_publication_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    short_code: Mapped[str] = mapped_column(String(32), unique=True)  # PB-xxxx
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    draft_id: Mapped[int] = mapped_column(ForeignKey("public_content_drafts.id", ondelete="CASCADE"))
    trace_id: Mapped[int | None] = mapped_column(ForeignKey("traces.id", ondelete="SET NULL"), nullable=True)

    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)  # 幂等: 同一草稿+版本只发一次
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)            # True = 只计划不动网络

    # GitHub 实际动作记录
    target_repo: Mapped[str] = mapped_column(String(255))
    branch: Mapped[str] = mapped_column(String(255))                 # 工作分支名
    base_branch: Mapped[str] = mapped_column(String(255), default="master")
    base_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    head_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)   # 发布落地的 commit
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 构建追踪(GitHub Actions / commit statuses)
    build_status: Mapped[str] = mapped_column(String(16), default="NONE")   # NONE/PENDING/RUNNING/SUCCESS/FAILURE
    build_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(32), default="CREATED")      # CREATED/PR_CREATED/BUILDING/PUBLISHED/FAILED/ROLLED_BACK
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 内容快照(发布时的 body/meta, 便于回滚与审计)
    markdown_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    json_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # 回滚
    rollback_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rollback_to_publication_id: Mapped[int | None] = mapped_column(
        ForeignKey("content_publication_runs.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    draft = relationship("PublicContentDraft", back_populates="publications")


class ContentPublicationRule(Base):
    """工作空间级发布规则(license_policy / auto_merge / fact_check_strictness)。"""

    __tablename__ = "content_publication_rules"
    __table_args__ = (UniqueConstraint("workspace_id", "key", name="uq_rule_ws_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(64))      # license_policy / auto_merge / fact_check_strictness
    value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ContentReleaseAudit(Base):
    """发布审计日志(append-only): 谁在什么时间做了什么。"""

    __tablename__ = "content_release_audit"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    draft_id: Mapped[int | None] = mapped_column(ForeignKey("public_content_drafts.id", ondelete="SET NULL"), nullable=True)
    publication_id: Mapped[int | None] = mapped_column(ForeignKey("content_publication_runs.id", ondelete="SET NULL"), nullable=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    action: Mapped[str] = mapped_column(String(32))   # VALIDATE/APPROVE/REJECT/PUBLISH/MERGE/ROLLBACK
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
