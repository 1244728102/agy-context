"""Pure-Python MCP App server for agy-context."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyzer  # noqa: E402

APP_URI = "ui://agy-context/app.html"
UI_DIR = Path(__file__).resolve().parent.parent / "ui"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "open_context_dashboard",
        "title": "AGY 上下文洞察",
        "description": (
            "打开 AGY 上下文洞察面板（跨会话总览、上下文构成与趋势、Token/费用统计、文件/命令活动及多 Agent 编队）。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "view": {
                    "type": "string",
                    "enum": ["insights", "session", "fleet"],
                    "description": "Initial dashboard tab to display.",
                },
                "conversation_id": {
                    "type": "string",
                    "description": "Optional Antigravity conversation UUID to inspect.",
                },
                "days": {
                    "type": "integer",
                    "description": "Time range in days for Insights overview (default 7).",
                },
            },
        },
        "_meta": {
            "ui": {"resourceUri": APP_URI},
            "com.google.antigravity/ui": {
                "entrypoints": [
                    {"type": "aux_pane", "title": "上下文洞察"},
                    {"type": "global", "title": "上下文洞察"},
                ]
            },
        },
    },
    {
        "name": "inspect_session_context",
        "title": "检查当前会话上下文",
        "description": (
            "Inspect a specific Antigravity conversation's context composition, per-step token trend, "
            "compactions, file activity, and command executions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {
                    "type": "string",
                    "description": "Conversation UUID (defaults to most recently active conversation).",
                },
            },
        },
        "_meta": {"ui": {"resourceUri": APP_URI}},
    },
    {
        "name": "app_get_overview",
        "description": "App-only: fetch cross-session context insights overview.",
        "inputSchema": {
            "type": "object",
            "properties": {"days": {"type": "integer"}},
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "app_get_session",
        "description": "App-only: fetch full session context analysis.",
        "inputSchema": {
            "type": "object",
            "properties": {"conversation_id": {"type": "string"}},
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "app_get_step_detail",
        "description": "App-only: fetch untruncated DNA step breakdown for Context Browser.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string"},
                "step_index": {"type": "integer"},
            },
            "required": ["conversation_id", "step_index"],
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
    {
        "name": "app_get_fleet",
        "description": "App-only: fetch multi-agent fleet tree and collaboration timeline.",
        "inputSchema": {
            "type": "object",
            "properties": {"conversation_id": {"type": "string"}},
        },
        "_meta": {"ui": {"visibility": ["app"]}},
    },
]


def load_app_html() -> str:
    html = (UI_DIR / "app.html").read_text(encoding="utf-8")
    sdk = (UI_DIR / "ext-apps.js").read_text(encoding="utf-8")
    idx = sdk.rfind("export{")
    if idx != -1:
        sdk = (
            sdk[:idx]
            + "return { App: oc, applyDocumentTheme: uc, applyHostStyleVariables: tc, applyHostFonts: gc, getDocumentTheme: $c };"
        )
    wrapped = f"const {{ App, applyDocumentTheme, applyHostStyleVariables }} = (() => {{\n{sdk}\n}})();"
    return html.replace("/*__EXT_APPS_SDK__*/", wrapped, 1)


def _resolve_conv_id(conv_id: str | None) -> str | None:
    if conv_id:
        return conv_id
    convs = analyzer.list_conversations()
    return convs[0]["id"] if convs else None


def handle_tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "open_context_dashboard":
        view = args.get("view") or "insights"
        days = int(args["days"]) if "days" in args and args["days"] is not None else 7
        conv_id = _resolve_conv_id(args.get("conversation_id"))
        overview = analyzer.get_overview(days=days)
        sc: dict[str, Any] = {"view": view, "overview": overview}
        summary_lines = [
            f"AGY Context ({view}): {overview['total_sessions']} conversations.",
            f"Last {days}d: {overview['kpis']['total_billed']:,} billed tokens, "
            f"${overview['kpis']['estimated_cost']:.3f} est. cost, "
            f"{overview['kpis']['cache_hit_pct']}% cache hit rate.",
        ]
        if view == "session" and conv_id:
            sess = analyzer.analyze_conversation(conv_id)
            sc["session"] = sess
            summary_lines.append(
                f"Session {conv_id[:8]}: {sess['stats']['current_context']:,} ctx tokens "
                f"({sess['stats']['context_pct']}%), {sess['stats']['steps']} steps."
            )
        elif view == "fleet" and conv_id:
            fleet = analyzer.get_fleet(conv_id)
            sc["fleet"] = fleet
            summary_lines.append(f"Fleet: {len(fleet['nodes'])} agents, {len(fleet['timeline'])} events.")
        return {
            "content": [{"type": "text", "text": "\n".join(summary_lines)}],
            "structuredContent": sc,
        }

    if name == "inspect_session_context":
        conv_id = _resolve_conv_id(args.get("conversation_id"))
        if not conv_id:
            return {"content": [{"type": "text", "text": "No conversations found."}], "isError": True}
        sess = analyzer.analyze_conversation(conv_id)
        overview = analyzer.get_overview(days=7)
        st = sess["stats"]
        txt = (
            f"Session {conv_id[:8]} ({sess['primary_model']}): "
            f"{st['current_context']:,} / {sess['window_size']:,} ctx ({st['context_pct']}%), "
            f"{st['turns']} turns, {st['steps']} steps, {st['cache_hit_pct']}% cache hit, "
            f"${st['estimated_cost']:.3f} est. cost."
        )
        return {
            "content": [{"type": "text", "text": txt}],
            "structuredContent": {"view": "session", "session": sess, "overview": overview},
        }

    if name == "app_get_overview":
        days = int(args["days"]) if "days" in args and args["days"] is not None else 7
        overview = analyzer.get_overview(days=days)
        return {"content": [{"type": "text", "text": "ok"}], "structuredContent": {"overview": overview}}

    if name == "app_get_session":
        conv_id = _resolve_conv_id(args.get("conversation_id"))
        if not conv_id:
            return {"content": [{"type": "text", "text": "No conversation"}], "isError": True}
        sess = analyzer.analyze_conversation(conv_id)
        return {"content": [{"type": "text", "text": "ok"}], "structuredContent": {"session": sess}}

    if name == "app_get_step_detail":
        conv_id = args.get("conversation_id")
        step_index = int(args.get("step_index", 0))
        detail = analyzer.get_step_detail(conv_id, step_index)
        return {"content": [{"type": "text", "text": "ok"}], "structuredContent": {"detail": detail}}

    if name == "app_get_fleet":
        conv_id = _resolve_conv_id(args.get("conversation_id"))
        fleet = analyzer.get_fleet(conv_id)
        return {"content": [{"type": "text", "text": "ok"}], "structuredContent": {"fleet": fleet}}

    return {"content": [{"type": "text", "text": f"Unknown tool: {name}"}], "isError": True}


def write_msg(payload: dict[str, Any]) -> None:
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    sys.stdout.buffer.write(line.encode("utf-8"))
    sys.stdout.buffer.flush()


def main() -> None:
    while True:
        raw = sys.stdin.buffer.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue

        req_id = req.get("id")
        method = req.get("method", "")
        params = req.get("params") or {}

        # Notifications have no id
        if req_id is None:
            continue

        try:
            if method == "initialize":
                proto = params.get("protocolVersion") or "2024-11-05"
                result = {
                    "protocolVersion": proto,
                    "capabilities": {"tools": {}, "resources": {}},
                    "serverInfo": {"name": "agy-context", "version": "0.1.0"},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                result = handle_tool_call(params.get("name", ""), params.get("arguments") or {})
            elif method == "resources/list":
                result = {
                    "resources": [
                        {
                            "uri": APP_URI,
                            "name": "AGY Context UI",
                            "mimeType": "text/html;profile=mcp-app",
                            "_meta": {"ui": {"prefersBorder": False}},
                        }
                    ]
                }
            elif method == "resources/read":
                uri = params.get("uri")
                if uri != APP_URI:
                    write_msg({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": f"Unknown resource: {uri}"}})
                    continue
                result = {
                    "contents": [
                        {
                            "uri": APP_URI,
                            "mimeType": "text/html;profile=mcp-app",
                            "text": load_app_html(),
                            "_meta": {"ui": {"prefersBorder": False}},
                        }
                    ]
                }
            else:
                write_msg({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}})
                continue

            write_msg({"jsonrpc": "2.0", "id": req_id, "result": result})
        except Exception as exc:
            write_msg({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32603, "message": str(exc)}})


if __name__ == "__main__":
    main()
