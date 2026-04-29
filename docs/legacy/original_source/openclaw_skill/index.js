/**
 * OpenClaw Skill — AI 自動策略開發系統
 * 接收訊息 → 解析指令 → POST 到 FastAPI → 格式化回傳
 */

const FASTAPI_BASE = process.env.FASTAPI_URL || "http://localhost:8080";

const ROUTES = {
  run:      (args) => ({ method: "POST", path: `/api/run`,            body: { cycles: parseInt(args[0]) || 5 } }),
  backtest: (args) => ({ method: "POST", path: `/api/backtest/${args[0]}`, body: {} }),
  generate: (_)    => ({ method: "POST", path: `/api/generate`,       body: {} }),
  optimize: (args) => ({ method: "POST", path: `/api/optimize/${args[0]}`, body: {} }),
  report:   (args) => ({ method: "GET",  path: `/api/report/${args[0]}`,   body: null }),
  list:     (_)    => ({ method: "GET",  path: `/api/strategies`,      body: null }),
  top:      (_)    => ({ method: "GET",  path: `/api/strategies/top`,  body: null }),
  status:   (_)    => ({ method: "GET",  path: `/api/status`,          body: null }),
};

function parseCommand(text) {
  const match = text.trim().match(/^\/(\w+)(.*)$/);
  if (!match) return null;
  const cmd = match[1].toLowerCase();
  const args = match[2].trim().split(/\s+/).filter(Boolean);
  return { cmd, args };
}

async function handleMessage(message) {
  const parsed = parseCommand(message.text);
  if (!parsed) return "❓ 請輸入有效指令（/run /backtest /generate /optimize /report /list /top /status）";

  const { cmd, args } = parsed;
  if (!ROUTES[cmd]) return `❓ 未知指令：/${cmd}`;

  const { method, path, body } = ROUTES[cmd](args);
  const url = `${FASTAPI_BASE}${path}`;

  try {
    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json();
    return formatReply(cmd, data);
  } catch (err) {
    return `❌ 系統錯誤：${err.message}`;
  }
}

function formatReply(cmd, data) {
  // TODO: 依各 cmd 格式化回傳文字
  return JSON.stringify(data, null, 2);
}

module.exports = { handleMessage };
