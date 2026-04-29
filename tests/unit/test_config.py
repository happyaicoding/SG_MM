"""tests/unit/test_config.py — Settings 配置載入器單元測試。"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.core.config import Settings, get_settings


class TestDefaultValues:
    def test_environment_default(self) -> None:
        s = Settings()
        assert s.environment == "development"

    def test_timezone_default(self) -> None:
        s = Settings()
        assert s.timezone == "Asia/Taipei"

    def test_nim_base_url_default(self) -> None:
        s = Settings()
        assert s.nim_base_url == "https://integrate.api.nvidia.com/v1"

    def test_daily_budget_default(self) -> None:
        s = Settings()
        assert s.daily_budget_usd == 10.0

    def test_api_port_default(self) -> None:
        s = Settings()
        assert s.api_port == 8000


class TestEnvOverride:
    def test_environment_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENVIRONMENT", "production")
        s = Settings()
        assert s.environment == "production"

    def test_budget_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DAILY_BUDGET_USD", "5.0")
        s = Settings()
        assert s.daily_budget_usd == 5.0

    def test_api_key_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
        s = Settings()
        assert s.anthropic_api_key == "sk-test-123"


class TestPathTypes:
    def test_sqlite_path_is_path_object(self) -> None:
        s = Settings()
        assert isinstance(s.sqlite_path, Path)

    def test_duckdb_path_is_path_object(self) -> None:
        s = Settings()
        assert isinstance(s.duckdb_path, Path)

    def test_data_path_is_path_object(self) -> None:
        s = Settings()
        assert isinstance(s.data_path, Path)

    def test_hf_home_is_path_object(self) -> None:
        s = Settings()
        assert isinstance(s.hf_home, Path)


class TestSingleton:
    def test_get_settings_returns_same_instance(self) -> None:
        get_settings.cache_clear()
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_get_settings_is_settings_instance(self) -> None:
        get_settings.cache_clear()
        s = get_settings()
        assert isinstance(s, Settings)
