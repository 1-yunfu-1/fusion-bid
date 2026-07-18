"""应用配置 — 通过环境变量加载，密钥不得硬编码."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 目录
BACKEND_DIR = Path(__file__).resolve().parents[2]
# 项目根（fusion-bid / feishu）
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    """全局设置."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="FusionBid智标聚合助手", alias="APP_NAME")
    app_env: Literal["development", "test", "production"] = Field(
        default="development", alias="APP_ENV"
    )
    app_debug: bool = Field(default=True, alias="APP_DEBUG")
    app_host: str = Field(default="127.0.0.1", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    app_timezone: str = Field(default="Asia/Shanghai", alias="APP_TIMEZONE")
    app_version: str = Field(default="1.0.0", alias="APP_VERSION")
    # 当前交付阶段标识（仅文档/展示，非业务逻辑开关）
    app_phase: str = Field(default="phase8-integration", alias="APP_PHASE")

    database_url: str = Field(
        default=f"sqlite+aiosqlite:///{(PROJECT_ROOT / 'data' / 'fusion_bid.db').as_posix()}",
        alias="DATABASE_URL",
    )

    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        alias="CORS_ORIGINS",
    )

    data_dir: Path = Field(default=PROJECT_ROOT / "data", alias="DATA_DIR")
    reports_dir: Path = Field(default=PROJECT_ROOT / "data" / "reports", alias="REPORTS_DIR")
    browser_states_dir: Path = Field(
        default=PROJECT_ROOT / "data" / "browser_states",
        alias="BROWSER_STATES_DIR",
    )
    fixtures_dir: Path = Field(default=PROJECT_ROOT / "data" / "fixtures", alias="FIXTURES_DIR")

    # --- 大模型：优先云端/兼容 API，其次本地 Ollama，最后规则降级 ---
    llm_enabled: bool = Field(default=True, alias="LLM_ENABLED")
    llm_base_url: str = Field(default="https://api.openai.com/v1", alias="LLM_BASE_URL")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")
    llm_timeout: int = Field(default=60, alias="LLM_TIMEOUT")
    # 解析通道顺序，逗号分隔：api,ollama,rule
    llm_prefer_order: str = Field(default="api,ollama,rule", alias="LLM_PREFER_ORDER")

    # 本地 Ollama（OpenAI 兼容 /v1 或原生 API）
    ollama_enabled: bool = Field(default=True, alias="OLLAMA_ENABLED")
    ollama_base_url: str = Field(default="http://127.0.0.1:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="qwen2.5:3b", alias="OLLAMA_MODEL")
    ollama_timeout: int = Field(default=120, alias="OLLAMA_TIMEOUT")

    http_timeout: int = Field(default=30, alias="HTTP_TIMEOUT")
    http_max_retries: int = Field(default=3, alias="HTTP_MAX_RETRIES")
    crawl_max_concurrency: int = Field(default=3, alias="CRAWL_MAX_CONCURRENCY")
    crawl_rate_limit_per_second: float = Field(default=1.0, alias="CRAWL_RATE_LIMIT_PER_SECOND")

    # --- CEBPUB 公开详情：专用普通 Chrome/Edge + 本机 CDP ---
    cebpub_browser_mode: Literal["managed", "legacy"] = Field(
        default="managed", alias="CEBPUB_BROWSER_MODE"
    )
    cebpub_browser_timeout_seconds: int = Field(
        default=60, ge=15, le=300, alias="CEBPUB_BROWSER_TIMEOUT_SECONDS"
    )
    cebpub_browser_concurrency: int = Field(
        default=2, ge=1, le=4, alias="CEBPUB_BROWSER_CONCURRENCY"
    )
    llm_extraction_concurrency: int = Field(
        default=2, ge=1, le=8, alias="LLM_EXTRACTION_CONCURRENCY"
    )
    cebpub_site_block_threshold: int = Field(
        default=2, ge=1, le=5, alias="CEBPUB_SITE_BLOCK_THRESHOLD"
    )

    # --- 登录态数据源（Playwright 手动登录 + storage state）---
    login_source_enabled: bool = Field(default=True, alias="LOGIN_SOURCE_ENABLED")
    login_source_home_url: str = Field(
        default="https://www.chinabidding.cn/",
        alias="LOGIN_SOURCE_HOME_URL",
    )
    login_source_login_url: str = Field(
        default="https://www.chinabidding.cn/",
        alias="LOGIN_SOURCE_LOGIN_URL",
    )
    # 支持 {keyword} / {kw} / {region} 占位
    login_source_search_url: str = Field(
        default="https://www.chinabidding.cn/search/?q={keyword}",
        alias="LOGIN_SOURCE_SEARCH_URL",
    )
    login_source_state_file: str = Field(
        default="login_portal_state.json",
        alias="LOGIN_SOURCE_STATE_FILE",
    )
    login_source_max_items: int = Field(default=10, alias="LOGIN_SOURCE_MAX_ITEMS")
    login_source_login_markers: str = Field(
        default="请登录,登录后查看,立即登录,用户登录,会员登录",
        alias="LOGIN_SOURCE_LOGIN_MARKERS",
    )
    login_source_logged_in_markers: str = Field(
        default="退出登录,安全退出,个人中心,我的账号,注销",
        alias="LOGIN_SOURCE_LOGGED_IN_MARKERS",
    )

    @field_validator("data_dir", "reports_dir", "browser_states_dir", "fixtures_dir", mode="before")
    @classmethod
    def _resolve_path(cls, value: str | Path) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
        return path

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def ensure_directories(self) -> None:
        """确保数据目录存在."""
        for d in (self.data_dir, self.reports_dir, self.browser_states_dir, self.fixtures_dir):
            d.mkdir(parents=True, exist_ok=True)
        # SQLite 父目录
        if self.database_url.startswith("sqlite"):
            # sqlite+aiosqlite:///./path or absolute
            raw = self.database_url.split("///", 1)[-1]
            db_path = Path(raw)
            if not db_path.is_absolute():
                db_path = (BACKEND_DIR / db_path).resolve()
            db_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
