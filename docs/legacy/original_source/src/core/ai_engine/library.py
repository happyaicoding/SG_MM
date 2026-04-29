"""策略參考庫檔案層 — 從 library/<category>/*.yaml + .els 載入 ProvenStrategy。

規範：
    library/<category>/<name>.els   ← MultiCharts ELS 純文字（必填）
    library/<category>/<name>.yaml  ← 元資料（必填）

yaml 必填欄位：
    name        — 策略英文名（與 .els 相關，但用 yaml 為準）
    category    — 從父資料夾名推斷，但 yaml 仍應寫
    timeframe   — 1min / 15min / 60min / 1D
    direction   — both / long / short
    description — 策略邏輯說明

選填欄位：
    proven_period   — 實盤期間
    risk_features   — list of strings
    notes           — 實戰心得

Usage:
    from src.core.ai_engine.library import StrategyLibrary

    lib = StrategyLibrary()
    strategies = lib.load_all()           # → list[ProvenStrategy]
    print(f"載入 {len(strategies)} 支實戰策略")

    # 切換到自訂位置
    lib = StrategyLibrary(root=Path("custom/path"))
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_LIBRARY = _PROJECT_ROOT / "library"

_VALID_CATEGORIES = {
    "trend", "mean_reversion", "opening", "scalp", "swing", "pattern",
}
_VALID_HOLDING_TYPES = {"daytrade", "swing"}


@dataclass
class ProvenStrategy:
    """已上線實戰的策略，作為 LLM few-shot 範例。"""

    name:         str
    holding_type: str       # 大分類：daytrade / swing
    category:     str       # 小分類（與 holding_type 正交）：trend / mean_reversion / ...
    timeframe:    str
    direction:    str
    description:  str
    el_code:      str
    proven_period: str = ""
    risk_features: list[str] = field(default_factory=list)
    notes:        str = ""

    # 內部用
    yaml_path:    str = ""
    els_path:     str = ""

    def to_embedding_text(self) -> str:
        """組合用於嵌入的文字（E2 設計：元資料 + EL 程式碼全文）。

        順序很重要：先放結構化元資料（讓嵌入向量被高層概念主導），
        再放程式碼細節。這樣短查詢「trend with ATR stop」也能匹到對的策略。
        """
        risk_block = "\n".join(f"  - {r}" for r in self.risk_features) if self.risk_features else "(none)"
        return (
            f"Name: {self.name}\n"
            f"Holding Type: {self.holding_type}\n"
            f"Category: {self.category}\n"
            f"Timeframe: {self.timeframe}\n"
            f"Direction: {self.direction}\n"
            f"Description:\n{self.description}\n"
            f"Risk Features:\n{risk_block}\n"
            f"Notes: {self.notes}\n\n"
            f"--- EasyLanguage Code ---\n"
            f"{self.el_code}"
        )

    def to_prompt_block(self) -> str:
        """格式化為 LLM prompt 內可直接吃的 markdown 區塊。"""
        risk_lines = "\n".join(f"  - {r}" for r in self.risk_features) if self.risk_features else "  - (未列出)"
        return (
            f"### {self.name} [{self.holding_type} / {self.category} / {self.timeframe} / {self.direction}]\n"
            f"**描述**：{self.description.strip()}\n\n"
            f"**風控特性**：\n{risk_lines}\n\n"
            f"**EasyLanguage 原始碼**：\n"
            f"```easylanguage\n{self.el_code.strip()}\n```\n"
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        # yaml/els path 保留以便除錯，但不影響 JSON 內容
        return d


class StrategyLibrary:
    """從 library/ 資料夾載入 ProvenStrategy 的檔案層。"""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or _DEFAULT_LIBRARY
        self.root.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> list[ProvenStrategy]:
        """掃描 library/<holding_type>/*.yaml，配對 .els，回傳所有策略。

        - 缺 .yaml 或 .els 的略過並 warning
        - 缺必填欄位的略過並 warning
        - holding_type 不在 {daytrade, swing} 的略過
        - category 不在 6 類預設的會 warning（仍接受）
        - holding_type 與父資料夾不符時，以 yaml 為準（並 warning）
        """
        strategies: list[ProvenStrategy] = []
        # 兩層結構：library/<holding_type>/<name>.yaml
        yaml_files = sorted(self.root.glob("*/*.yaml"))

        for yp in yaml_files:
            els_path = yp.with_suffix(".els")
            if not els_path.exists():
                logger.warning("找不到對應 .els：%s（略過）", yp.name)
                continue

            try:
                meta = yaml.safe_load(yp.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                logger.warning("yaml 解析失敗 %s：%s（略過）", yp.name, exc)
                continue

            # 必填欄位檢查
            required = ("name", "holding_type", "category", "timeframe",
                        "direction", "description")
            missing = [f for f in required if not meta.get(f)]
            if missing:
                logger.warning("%s 缺必填欄位 %s（略過）", yp.name, missing)
                continue

            # holding_type 值驗證（嚴格）
            holding = str(meta["holding_type"]).strip().lower()
            if holding not in _VALID_HOLDING_TYPES:
                logger.warning(
                    "%s holding_type=%s 不在 %s 中（略過）",
                    yp.name, holding, sorted(_VALID_HOLDING_TYPES),
                )
                continue

            # category 值驗證（軟性，僅 warn）
            category = str(meta["category"]).strip().lower()
            if category not in _VALID_CATEGORIES:
                logger.warning(
                    "%s category=%s 不在預設 %s 中（仍接受，但 LLM 可能不熟）",
                    yp.name, category, sorted(_VALID_CATEGORIES),
                )

            # holding_type 與父資料夾比對
            folder_name = yp.parent.name.lower()
            if folder_name in _VALID_HOLDING_TYPES and folder_name != holding:
                logger.warning(
                    "%s yaml holding_type=%s 與資料夾 %s 不符；以 yaml 為準",
                    yp.name, holding, folder_name,
                )

            try:
                el_code = els_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                # ELS 從 PLEditor 匯出有時是 cp950
                el_code = els_path.read_text(encoding="cp950", errors="replace")

            strategies.append(ProvenStrategy(
                name          = str(meta["name"]),
                holding_type  = holding,
                category      = category,
                timeframe     = str(meta["timeframe"]),
                direction     = str(meta["direction"]),
                description   = str(meta["description"]).strip(),
                el_code       = el_code,
                proven_period = str(meta.get("proven_period", "")),
                risk_features = list(meta.get("risk_features", []) or []),
                notes         = str(meta.get("notes", "")).strip(),
                yaml_path     = str(yp),
                els_path      = str(els_path),
            ))

        logger.info("StrategyLibrary 載入 %d 支實戰策略 (root=%s)",
                    len(strategies), self.root)
        return strategies

    def load_by_category(self, category: str) -> list[ProvenStrategy]:
        """只載入指定 category 的策略（小分類過濾）。"""
        return [s for s in self.load_all() if s.category == category]

    def load_by_holding_type(self, holding_type: str) -> list[ProvenStrategy]:
        """只載入指定 holding_type 的策略（大分類過濾）。"""
        return [s for s in self.load_all() if s.holding_type == holding_type]
