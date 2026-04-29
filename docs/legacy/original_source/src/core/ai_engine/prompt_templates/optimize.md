# 策略優化 Prompt

## 任務
分析回測績效報告，找出虧損原因，提出 3 種以上具體改善方案。

## 輸入
- 策略原始碼
- 回測績效（Sharpe、MaxDD、PF、月度損益分析）
- 現有參數設定

## 輸出格式
```json
{
  "analysis": "虧損原因分析...",
  "suggestions": [
    { "title": "方案1", "description": "...", "new_params": {...}, "expected_improvement": "..." },
    { "title": "方案2", ... },
    { "title": "方案3", ... }
  ]
}
```

## 規範
- 每個方案參數數量 ≤ 5 個
- 說明預期效果（如 Sharpe 預計提升 X%）
- 優先調整進出場邏輯，而非單純優化參數
