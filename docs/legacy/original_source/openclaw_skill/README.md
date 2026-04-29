# OpenClaw Skill — 台指期 AI 策略系統

## 安裝

1. 確認 FastAPI 伺服器已啟動：`python main.py web --port 8080`
2. 將本目錄複製至 OpenClaw skills 目錄
3. 重啟 OpenClaw Gateway

## 環境變數

```
FASTAPI_URL=http://localhost:8080   # 可覆寫預設值
```

## 支援指令

| 指令 | 說明 |
|---|---|
| `/run <N>` | 啟動 N 輪 Agent Loop |
| `/backtest <id>` | 執行全期回測 |
| `/generate` | AI 生成新策略 |
| `/optimize <id>` | AI 優化策略參數 |
| `/report <id>` | 產出 HTML 報表連結 |
| `/list` | 列出策略庫 |
| `/top` | 績效前 5 名 |
| `/status` | 系統狀態 |
