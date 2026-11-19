"""CareerPilot 后端配置。

所有配置来自环境变量或 .env 文件; 生产环境绝不使用默认密码。
"""
from functools import lru_cache
import re

from pydantic import Field, field_validator

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 应用
    app_name: str = "CareerPilot API"
    api_v1_prefix: str = "/api/v1"
    debug: bool = True

    # 数据库(本地开发默认值; 生产通过 .env / 环境变量覆盖)
    database_url: str = "postgresql+asyncpg://careerpilot:careerpilot_dev@localhost:5432/careerpilot"
    db_echo: bool = False

    # 认证(MVP: API token 简单校验; 生产换 JWT/OAuth)
    api_token: str = "cp-dev-token"
    workspace_token_hashes: dict[str, str] = {}
    # 正式个人空间; Demo 仅由显式 X-Workspace: demo 访问。
    default_workspace_slug: str = "personal"

    # LLM 适配(空 = 规则引擎降级模式, 不依赖外部 LLM)
    llm_provider: str = ""          # deepseek | glm | ""(rule)
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""

    # Application runtime tutoring routes. An empty strong model disables that seat.
    study_flash_model: str = ""
    study_strong_model: str = ""
    study_strong_base_url: str = ""
    study_strong_api_key: str = ""
    study_strong_provider: str = ""
    study_output_budget: int = 6000
    study_timeout_seconds: int = 120
    study_flash_context: int = 24000
    study_strong_context: int = 48000
    # Explicit deployment capabilities. Chat uses text SSE and bounded local tools.
    study_flash_capabilities: list[str] = ["text", "stream"]
    study_strong_capabilities: list[str] = ["text", "stream", "structured_text", "code_reasoning"]
    study_project_roots: dict[str, str] = {}
    # Frozen onto each new AI scan. Streaming may keep working while the model
    # reasons, with a bounded wall-clock deadline independent of scan budgets.
    project_scan_stage_timeout_seconds: int = Field(default=600, ge=180, le=600)

    # 评分引擎
    scorer_model_version: str = "rule-v1"

    # 公开内容发布(GitHub 内容仓库, 独立于本地私人数据系统)
    github_api_url: str = "https://api.github.com"
    github_token: str = ""                          # 环境变量 GITHUB_TOKEN(gh auth token)
    gitcode_token: str = ""                         # 环境变量 GITCODE_TOKEN(gitcode 个人访问令牌)
    portal_target_repo: str = "Lueyu/agent-interview"   # 内容仓库 owner/repo
    portal_base_branch: str = "master"              # 发布目标默认分支
    portal_content_dir: str = "portal/content"      # 仓库内内容目录
    portal_auto_merge: bool = False

    @field_validator("workspace_token_hashes")
    @classmethod
    def validate_workspace_tokens(cls, value):
        if any(not slug.strip() or not re.fullmatch(r"[0-9a-f]{64}", digest) for slug, digest in value.items()):
            raise ValueError("Expected workspace slugs with lowercase SHA-256 token digests")
        if len(set(value.values())) != len(value):
            raise ValueError("Each workspace needs a distinct token")
        return value

    @property
    def public_writes_enabled(self) -> bool:
        """本轮固定本地模式。环境变量、DB 规则和旧 token 均不能开启公开写入。"""
        return False

    @property
    def evolution_activation_enabled(self) -> bool:
        """保留候选/历史版本, 但不启用或消费自适应策略。"""
        return False

    @property
    def sync_database_url(self) -> str:
        """Alembic / 同步工具用的 psycopg 风格 URL。"""
        return self.database_url.replace("+asyncpg", "")


@lru_cache
def get_settings() -> Settings:
    return Settings()
