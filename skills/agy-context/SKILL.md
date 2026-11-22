---
name: agy-context
description: Inspect Antigravity context composition, token & cost evolution, compaction events, file/command activity, and multi-agent subagent fleet collaboration. Activate when the user asks about context usage, token breakdown, context window, /context, /agy-context, or subagent fleet timeline.
---

# AGY Context Insight

Use the `agy-context` MCP App tools to open the interactive Context Insights dashboard:

- **Cross-session overview / general dashboard**: Call `open_context_dashboard` (optionally with `view: "insights"`).
- **Current or specific session context**: Call `inspect_session_context` (or `open_context_dashboard` with `view: "session"` and `conversation_id` if known).
- **Multi-agent fleet & subagent collaboration**: Call `open_context_dashboard` with `view: "fleet"` (and `conversation_id` if known).

## Guidelines

1. Always invoke the MCP App tool so the interactive UI renders for the user (inline or in the side pane).
2. Keep your text reply concise—summarize only key highlights from the tool's text output rather than dumping raw JSON.
3. If the user asks about a specific session or the current conversation, pass its `conversation_id` when available; otherwise omit `conversation_id` and the server will automatically select the most recently active conversation.
