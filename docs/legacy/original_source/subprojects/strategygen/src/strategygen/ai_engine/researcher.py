"""AI 研究員 — 搜尋台指期策略資訊，支援 URL 知識庫 + web_search。

功能：
    1. URL 知識庫管理
       add_url(url, tags)   — 抓取網頁內容並儲存至 SQLite url_knowledge 表
       remove_url(url)      — 從知識庫刪除
       list_urls()          — 列出所有已儲存網址
       refetch_url(url)     — 重新抓取並更新內容

    2. 研究摘要生成
       research(topic)      — 結合知識庫內容 + web_search，回傳 JSON 結構摘要

web_search 說明：
    僅 ClaudeClient 支援 web_search tool（Anthropic 原生功能）。
    MiniMax 或其他供應商會自動略過 web_search，僅使用知識庫內容。

Usage:
    from src.core.ai_engine.researcher import Researcher
    from src.db.init_db import get_db

    db = next(get_db())
    r = Researcher(db_session=db)

    # 管理知識庫
    r.add_url("https://www.ptt.cc/bbs/Stock/M.1234567.A.html", tags="台指期,趨勢")
    r.add_url("https://blog.quant.tw/macd-tx/", tags="MACD")
    r.list_urls()

    # 研究
    summary = r.research("台指期 MACD 交叉策略")
    print(summary)
"""
from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx

from strategygen.ai_engine.client import BaseLLMClient, ClaudeClient, create_llm_client

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_TEMPLATE_PATH = Path(__file__).parent / "prompt_templates" / "research.md"

# web_search tool 定義（僅 Claude 支援）
_WEB_SEARCH_TOOL: dict = {
    "type": "web_search_20250305",
    "name": "web_search",
}

# 每個 URL 內容注入 prompt 的最大字元數
_MAX_CONTENT_PER_URL = 3000
# 知識庫注入 prompt 的最多 URL 數量
_MAX_URLS_IN_PROMPT = 8


class Researcher:
    """AI 研究員 — URL 知識庫管理 + 研究摘要生成。

    Args:
        client:     LLM 客戶端（None 時從 config.yaml 建立）
        db_session: SQLAlchemy Session（None 時使用本地 JSON 備援）
    """

    def __init__(
        self,
        client: BaseLLMClient | None = None,
        db_session: "Session | None" = None,
    ) -> None:
        self._client = client or create_llm_client()
        self._db = db_session
        self._system_prompt = self._load_system_prompt()

    # ── URL 知識庫管理 ────────────────────────────────────────────

    def add_url(self, url: str, tags: str = "", force_refetch: bool = False) -> dict:
        """抓取網頁內容並儲存至知識庫。

        Args:
            url:          目標網址
            tags:         逗號分隔標籤，如 "台指期,MACD,趨勢"
            force_refetch: True 時強制重新抓取已存在的網址

        Returns:
            dict 含 url / title / content_length / tags / status
        """
        # JSON 模式：一次讀取所有記錄，避免 find + upsert 各讀一次
        if self._db is None:
            all_json = self._load_json()
            existing: dict | None = next((r for r in all_json if r["url"] == url), None)
        else:
            all_json = None
            existing = self._find_url_db(url)

        if existing and not force_refetch:
            logger.info("URL 已存在，略過抓取：%s", url)
            return {
                "url": url,
                "title": existing.get("title", ""),
                "content_length": len(existing.get("content", "")),
                "tags": existing.get("tags", ""),
                "status": "already_exists",
            }

        # 抓取網頁
        title, content, ok = self._fetch_url(url)

        record = {
            "id":           existing.get("id") if existing else str(uuid4()),
            "url":          url,
            "title":        title,
            "content":      content,
            "tags":         tags,
            "added_at":     existing.get("added_at", _utcnow_str()) if existing else _utcnow_str(),
            "last_fetched": _utcnow_str(),
            "fetch_ok":     ok,
        }

        if all_json is not None:
            # JSON 模式：直接用已讀取的列表，不再重讀
            idx = next((i for i, r in enumerate(all_json) if r["url"] == url), None)
            if idx is not None:
                all_json[idx] = record
            else:
                all_json.append(record)
            self._save_json(all_json)
        else:
            self._upsert_url_db(record)

        logger.info(
            "URL %s：%s（%d 字元）tags=%s",
            "更新" if existing else "新增",
            url, len(content), tags,
        )
        return {
            "url":            url,
            "title":          title,
            "content_length": len(content),
            "tags":           tags,
            "status":         "updated" if existing else "added",
            "fetch_ok":       ok,
        }

    def remove_url(self, url: str) -> bool:
        """從知識庫刪除指定網址。

        Returns:
            True 表示成功刪除；False 表示網址不存在
        """
        removed = self._delete_url(url)
        if removed:
            logger.info("已從知識庫移除：%s", url)
        else:
            logger.warning("知識庫中找不到網址：%s", url)
        return removed

    def list_urls(self) -> list[dict]:
        """列出所有已儲存網址的摘要資訊。

        Returns:
            list of dict，含 url / title / tags / content_length / last_fetched / fetch_ok
        """
        records = self._load_all_urls()
        return [
            {
                "url":            r["url"],
                "title":          r.get("title", ""),
                "tags":           r.get("tags", ""),
                "content_length": len(r.get("content", "")),
                "last_fetched":   r.get("last_fetched", ""),
                "fetch_ok":       r.get("fetch_ok", True),
            }
            for r in records
        ]

    def refetch_url(self, url: str) -> dict:
        """重新抓取並更新知識庫中已存在的網址。"""
        return self.add_url(url, force_refetch=True)

    # ── 研究摘要生成 ──────────────────────────────────────────────

    def research(
        self,
        topic: str = "台指期量化策略",
        use_web_search: bool = True,
        tag_filter: str = "",
    ) -> str:
        """結合知識庫內容 + web_search，回傳 JSON 結構研究摘要。

        Args:
            topic:          研究主題
            use_web_search: True 時啟用 Claude web_search tool（MiniMax 自動略過）
            tag_filter:     只使用含此標籤的知識庫條目（空字串 = 全部）

        Returns:
            JSON 字串，格式參考 prompt_templates/research.md
        """
        # 組裝知識庫上下文
        kb_context = self._build_kb_context(tag_filter)

        user_msg = self._build_user_message(topic, kb_context)

        # 決定是否使用 web_search（僅 ClaudeClient 支援）
        tools = None
        if use_web_search and isinstance(self._client, ClaudeClient):
            tools = [_WEB_SEARCH_TOOL]
            logger.info("研究啟用 web_search tool")
        elif use_web_search:
            logger.info(
                "LLM 為 %s，不支援 web_search tool，僅使用知識庫內容",
                type(self._client).__name__,
            )

        logger.info("開始研究：%s（知識庫條目：%d）", topic, self._count_urls())

        result = self._client.chat(
            messages=[{"role": "user", "content": user_msg}],
            system=self._system_prompt,
            max_tokens=2048,
            temperature=0.3,
            tools=tools,
        )
        return result

    # ── 內部：知識庫儲存（SQLite 優先，JSON 備援）────────────────

    def _find_url(self, url: str) -> dict | None:
        if self._db is not None:
            return self._find_url_db(url)
        return self._find_url_json(url)

    def _upsert_url(self, record: dict) -> None:
        if self._db is not None:
            self._upsert_url_db(record)
        else:
            self._upsert_url_json(record)

    def _delete_url(self, url: str) -> bool:
        if self._db is not None:
            return self._delete_url_db(url)
        return self._delete_url_json(url)

    def _load_all_urls(self) -> list[dict]:
        if self._db is not None:
            return self._load_all_urls_db()
        return self._load_all_urls_json()

    def _count_urls(self) -> int:
        """回傳知識庫條目數量，不讀取全部內容（供 log 使用）。"""
        if self._db is not None:
            from src.db.models import URLKnowledge
            return self._db.query(URLKnowledge).count()
        return len(self._load_json())

    # SQLite 實作
    def _find_url_db(self, url: str) -> dict | None:
        from src.db.models import URLKnowledge
        row = self._db.query(URLKnowledge).filter_by(url=url).first()
        return _row_to_dict(row) if row else None

    def _upsert_url_db(self, record: dict) -> None:
        from src.db.models import URLKnowledge
        row = self._db.query(URLKnowledge).filter_by(url=record["url"]).first()
        if row:
            for k, v in record.items():
                if k != "id":
                    setattr(row, k, v)
        else:
            self._db.add(URLKnowledge(**record))
        self._db.commit()

    def _delete_url_db(self, url: str) -> bool:
        from src.db.models import URLKnowledge
        row = self._db.query(URLKnowledge).filter_by(url=url).first()
        if row:
            self._db.delete(row)
            self._db.commit()
            return True
        return False

    def _load_all_urls_db(self) -> list[dict]:
        from src.db.models import URLKnowledge
        rows = self._db.query(URLKnowledge).order_by(URLKnowledge.added_at.desc()).all()
        return [_row_to_dict(r) for r in rows]

    # JSON 備援實作
    @staticmethod
    def _json_path() -> Path:
        p = Path(__file__).resolve().parents[3] / "data" / "url_knowledge.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _load_json(self) -> list[dict]:
        p = self._json_path()
        if not p.exists():
            return []
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save_json(self, records: list[dict]) -> None:
        self._json_path().write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _find_url_json(self, url: str) -> dict | None:
        return next((r for r in self._load_json() if r["url"] == url), None)

    def _upsert_url_json(self, record: dict) -> None:
        records = self._load_json()
        idx = next((i for i, r in enumerate(records) if r["url"] == record["url"]), None)
        if idx is not None:
            records[idx] = record
        else:
            records.append(record)
        self._save_json(records)

    def _delete_url_json(self, url: str) -> bool:
        records = self._load_json()
        new_records = [r for r in records if r["url"] != url]
        if len(new_records) == len(records):
            return False
        self._save_json(new_records)
        return True

    def _load_all_urls_json(self) -> list[dict]:
        return self._load_json()

    # ── 內部：網頁抓取 ────────────────────────────────────────────

    @staticmethod
    def _fetch_url(url: str, timeout: int = 15) -> tuple[str, str, bool]:
        """抓取網頁，回傳 (title, content, success)。"""
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
                ),
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            }
            resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
            resp.raise_for_status()

            html_text = resp.text
            title = _extract_title(html_text) or url
            content = _extract_text(html_text)

            # 截斷到最大長度
            if len(content) > _MAX_CONTENT_PER_URL * 2:
                content = content[: _MAX_CONTENT_PER_URL * 2]

            return title, content, True

        except Exception as exc:
            logger.warning("抓取網頁失敗（%s）：%s", url, exc)
            return url, "", False

    # ── 內部：Prompt 組裝 ─────────────────────────────────────────

    def _build_kb_context(self, tag_filter: str = "") -> str:
        """從知識庫組裝上下文字串。"""
        records = self._load_all_urls()

        if tag_filter:
            records = [
                r for r in records
                if tag_filter.lower() in (r.get("tags") or "").lower()
            ]

        # 只取有內容且抓取成功的條目
        records = [r for r in records if r.get("content") and r.get("fetch_ok", True)]

        if not records:
            return ""

        # 最多注入 _MAX_URLS_IN_PROMPT 個
        records = records[:_MAX_URLS_IN_PROMPT]

        parts = ["## 知識庫參考資料（使用者指定網址）\n"]
        for r in records:
            content = (r.get("content") or "")[:_MAX_CONTENT_PER_URL]
            parts.append(
                f"### [{r.get('title', r['url'])}]({r['url']})\n"
                f"標籤：{r.get('tags', '無')}\n"
                f"{content}\n"
            )
        return "\n".join(parts)

    def _build_user_message(self, topic: str, kb_context: str) -> str:
        parts = [f"## 研究主題\n{topic}"]
        if kb_context:
            parts.append(kb_context)
        parts.append(
            "請結合以上知識庫內容與你的搜尋結果，"
            "依 system prompt 格式輸出 JSON 研究摘要。"
        )
        return "\n\n".join(parts)

    def _load_system_prompt(self) -> str:
        if _TEMPLATE_PATH.exists():
            return _TEMPLATE_PATH.read_text(encoding="utf-8")
        return "你是一位台指期量化策略研究員，請輸出 JSON 格式的研究摘要。"


# ── 工具函式 ──────────────────────────────────────────────────────

def _utcnow_str() -> str:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None).isoformat()


def _row_to_dict(row) -> dict:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def _extract_title(html_text: str) -> str:
    """從 HTML 擷取 <title> 標籤內容。"""
    m = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
    if m:
        return html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()[:200]
    return ""


def _extract_text(html_text: str) -> str:
    """從 HTML 擷取純文字（移除 script/style/tag）。"""
    # 移除 script / style 區塊
    text = re.sub(
        r"<(script|style)[^>]*>.*?</(script|style)>",
        " ", html_text, flags=re.DOTALL | re.IGNORECASE,
    )
    # 移除所有 HTML 標籤
    text = re.sub(r"<[^>]+>", " ", text)
    # 解碼 HTML 實體
    text = html.unescape(text)
    # 壓縮空白
    text = re.sub(r"\s+", " ", text).strip()
    return text
