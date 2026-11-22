<h1 align="center">AGY Context Telemetry · AGY 上下文洞察与多 Agent 编队透视仪</h1>

<p align="center"><b>8-dimension token billing breakdown, step-by-step context evolution, command audit & multi-agent fleet topology telemetry for Google Antigravity (AGY).</b></p>

<p align="center">🥇 <b>全方位透视 Google Antigravity 上下文组成、8 维 Token 账单环形图、步级趋势与多 Agent 编队协作时间线。</b> 🥇</p>

<p align="center">
  <a href="./README.zh-CN.md">简体中文</a> ·
  <a href="./INSTALL.md">Install Guide</a> ·
  <a href="./CHANGELOG.md">Changelog</a> ·
  <a href="./LICENSE">License</a>
</p>

<p align="center">
  <a href="https://github.com/1244728102"><img src="https://img.shields.io/badge/author-1244728102%20(%E7%88%B1%E5%93%AD%E7%9A%84%E4%BC%8A%E8%95%BE%E5%A8%9C)-b69cf5?style=flat-square&logo=github" alt="Author"></a>
  <img src="https://img.shields.io/badge/version-0.1.0-e9c27a?style=flat-square" alt="Version">
  <img src="https://img.shields.io/badge/platform-Google%20Antigravity%20(AGY)-6a56b8?style=flat-square" alt="Platform">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="License"></a>
</p>

---

## ✨ Overview & Highlights

**AGY Context Telemetry · AGY 上下文洞察与多 Agent 编队透视仪 (`agy-context`)** — Antigravity 上下文洞察插件：透视上下文组成与演进、Token 与费用统计、文件与命令活动，以及多 Agent 编队协作。

# AGY Context Insight

Use the `agy-context` MCP App tools to open the interactive Context Insights dashboard:

- **Cross-session overview / general dashboard**: Call `open_context_dashboard` (optionally with `view: "insights"`).
- **Current or specific session context**: Call `inspect_session_context` (or `open_context_dashboard` with `view: "session"` and `conversation_id` if known).
- **Multi-agent fleet & subagent collaboration**: Call `open_context_dashboard` with `view: "fleet"` (and `conversation_id` if known).

## Guidelines

1. Always invoke the MCP App tool so the interactive UI renders for the user (inline or in the side pane).
2. Keep your text reply concise—summarize only key highlights from the tool's text output rather than dumping raw JSON.
3. If the user asks about a specific session or the current conversation, pass its `conversation_id` when available; otherwise omit `conversation_id` and the server will automatically select the most recently active conversation.


---

## 🚀 Quick Install

**Windows PowerShell**:
```powershell
git clone https://github.com/1244728102/agy-context.git "$env:USERPROFILE\.gemini\config\plugins\agy-context"
```

**macOS / Linux**:
```bash
git clone https://github.com/1244728102/agy-context.git "$HOME/.gemini/config/plugins/agy-context"
```

**Or hand the install to your AI agent**:
> Install and configure `agy-context` following https://github.com/1244728102/agy-context/blob/main/INSTALL.md

---

## 📂 Project Files (`13` files)

```text
agy-context/
├── .gitignore
├── assets/logo.svg
├── CHANGELOG.md
├── INSTALL.md
├── LICENSE
├── mcp_config.json
├── plugin.json
├── README.md
├── server/analyzer.py
├── server/mcp_server.py
├── skills/agy-context/SKILL.md
├── ui/app.html
├── ui/ext-apps.js
```

---

## 📜 License

MIT © [1244728102 (爱哭的伊蕾娜)](https://github.com/1244728102)
