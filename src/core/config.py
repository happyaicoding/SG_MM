"""src/core/config.py — AISMART 全域配置載入器。

以 Pydantic BaseSettings 實作，從 .env 讀取所有設定。
透過 get_settings() 取得 singleton 實例。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """AISMART 配置類別（從 .env 載入）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 執行環境
    environment: str = Field(default="development")
    timezone: str = Field(default="Asia/Taipei")

    # LLM API
    nim_api_key: str = Field(default="")
    nim_base_url: str = Field(default="https://integrate.api.nvidia.com/v1")
    minimax_api_key: str = Field(default="")
    minimax_base_url: str = Field(default="https://api.minimax.chat/v1")
    anthropic_api_key: str = Field(default="")

    # 資料路徑
    data_path: Path = Field(default=Path("./data"))
    sqlite_path: Path = Field(default=Path("./data/sqlite/main.db"))
    duckdb_path: Path = Field(default=Path("./data/duckdb/strategy_vectors.duckdb"))
    hf_home: Path = Field(default=Path("./data/models"))

    # MC Bridge
    mc_bridge_host: str = Field(default="127.0.0.1")
    mc_bridge_port: int = Field(default=8001)
    mc_dir: str = Field(default="C:/Program Files/TS Support/MultiCharts64")
    mc_studies_dir: str = Field(default="")

    # 預算控制
    daily_budget_usd: float = Field(default=10.0)
    hard_stop_usd: float = Field(default=10.0)

    # Web 服務
    api_port: int = Field(default=8000)
    webui_port: int = Field(default=3000)

    # Cloudflare Tunnel
    cf_tunnel_token: str = Field(default="")


@lru_cache
def get_settings() -> Settings:
    """取得全域唯一配置實例（從 .env 載入，lru_cache 保證 singleton）。

    Returns:
        Settings: 全域配置物件。
    """
    return Settings()
