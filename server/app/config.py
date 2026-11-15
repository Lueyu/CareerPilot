"""CareerPilot 后端配置。

所有配置来自环境变量或 .env 文件; 生产环境绝不使用默认密码。
"""
from functools import lru_cache

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
    # MVP 默认工作空间 slug(单用户演示; 多租户结构已就位)
    default_workspace_slug: str = "demo"

    # LLM 适配(空 = 规则引擎降级模式, 不依赖外部 LLM)
    llm_provider: str = ""          # deepseek | glm | ""(rule)
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""

    # 评分引擎
    scorer_model_version: str = "rule-v1"

    @property
    def sync_database_url(self) -> str:
        """Alembic / 同步工具用的 psycopg 风格 URL。"""
        return self.database_url.replace("+asyncpg", "")


@lru_cache
def get_settings() -> Settings:
    return Settings()
