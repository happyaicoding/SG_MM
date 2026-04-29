# 研究 Prompt

## 任務
使用 `web_search` 工具搜尋台指期相關量化策略資訊，整理成結構化摘要。

## 搜尋方向
1. 台指期量化交易策略（PTT 期貨板、股版討論）
2. 台指期技術分析方法（突破、均線、RSI、布林通道等）
3. 台灣期貨市場特性（日夜盤差異、結算日效應）
4. 相關量化研究論文（SSRN、arXiv）

## 輸出格式
```json
{
  "strategies": [
    {
      "name": "策略名稱",
      "logic": "進出場邏輯描述",
      "indicators": ["EMA", "RSI"],
      "source": "來源URL",
      "convertible": true
    }
  ],
  "market_insights": "台指期市場特性摘要",
  "recommended_strategy": "最推薦實作的策略及理由"
}
```
