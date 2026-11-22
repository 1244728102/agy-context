"""Antigravity conversation transcript analyzer for agy-context."""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
USER_REQ_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)
EXIT_CODE_RE = re.compile(r"Command exited with code (-?\d+)")

# Pricing per 1M tokens in USD (realistic Gemini API reference rates; 1 USD = 7.20 CNY)
MODEL_PRICING: list[tuple[str, dict[str, Any]]] = [
    (
        "gemini-4-hydrogen",
        {"window": 1_000_000, "input": 0.28, "cache": 0.07, "output": 1.10},
    ),
    (
        "gemini-3.8-flash",
        {"window": 1_000_000, "input": 0.10, "cache": 0.025, "output": 0.40},
    ),
    (
        "gemini-3.8-pro",
        {"window": 1_000_000, "input": 0.28, "cache": 0.07, "output": 1.10},
    ),
    (
        "gemini-3.5",
        {"window": 1_000_000, "input": 0.15, "cache": 0.0375, "output": 0.60},
    ),
]
DEFAULT_MODEL_INFO = {"window": 1_000_000, "input": 0.15, "cache": 0.0375, "output": 0.60}


def get_brain_dir() -> Path:
    env_dir = os.environ.get("AGY_BRAIN_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".gemini" / "antigravity" / "brain"


def get_model_info(model_name: str | None) -> dict[str, Any]:
    if not model_name:
        return DEFAULT_MODEL_INFO
    low = model_name.lower()
    for prefix, info in MODEL_PRICING:
        if prefix in low:
            return info
    return DEFAULT_MODEL_INFO


def estimate_cost(
    model_name: str | None, input_tokens: int, cache_read_tokens: int, output_tokens: int
) -> float:
    info = get_model_info(model_name)
    return (
        (input_tokens / 1_000_000.0) * info["input"]
        + (cache_read_tokens / 1_000_000.0) * info["cache"]
        + (output_tokens / 1_000_000.0) * info["output"]
    )


def estimate_tokens(text: Any) -> int:
    """Roughly estimate token count for mixed CJK / English text."""
    if not text:
        return 0
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return max(1, int(cjk * 1.3 + other / 3.8))


def parse_ts(ts_str: str | None) -> datetime | None:
    if not ts_str:
        return None
    try:
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def clean_user_text(raw: str | None) -> str:
    if not raw:
        return ""
    m = USER_REQ_RE.search(raw)
    text = m.group(1) if m else raw
    text = re.sub(r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>", "", text, flags=re.DOTALL)
    text = re.sub(r"<USER_SETTINGS_CHANGE>.*?</USER_SETTINGS_CHANGE>", "", text, flags=re.DOTALL)
    return text.strip()


def parse_args(args: Any) -> dict[str, Any]:
    if isinstance(args, dict):
        out: dict[str, Any] = {}
        for k, v in args.items():
            if isinstance(v, str) and len(v) >= 2 and v[0] == '"' and v[-1] == '"':
                try:
                    out[k] = json.loads(v)
                    continue
                except Exception:
                    pass
            out[k] = v
        return out
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.is_file():
        return records
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                continue
    return records


def list_conversations(brain_dir: Path | None = None) -> list[dict[str, Any]]:
    root = brain_dir or get_brain_dir()
    if not root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        tpath = entry / ".system_generated" / "logs" / "transcript.jsonl"
        if tpath.is_file():
            items.append(
                {
                    "id": entry.name,
                    "path": tpath,
                    "full_path": entry / ".system_generated" / "logs" / "transcript_full.jsonl",
                    "mtime": tpath.stat().st_mtime,
                }
            )
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def _tool_source_tag(tool_name: str) -> str:
    if tool_name.startswith("mcp_"):
        parts = tool_name.split("_", 2)
        return f"mcp:{parts[1]}" if len(parts) > 1 else "mcp"
    if tool_name in {"invoke_subagent", "send_message", "manage_subagents", "swarm", "run_workflow"}:
        return "multi-agent"
    if tool_name in {"read_file", "write_file", "edit_file", "run_command", "manage_task"}:
        return "core"
    return "builtin"


def _extract_command_program(cmd: str) -> str:
    if not cmd:
        return "shell"
    for line in cmd.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("$"):
            continue
        token = line.split()[0].strip("\"'`")
        token = os.path.basename(token)
        if token.lower().endswith(".exe"):
            token = token[:-4]
        return token or "shell"
    return "powershell"


def analyze_conversation(conv_id: str, brain_dir: Path | None = None) -> dict[str, Any]:
    root = brain_dir or get_brain_dir()
    logs_dir = root / conv_id / ".system_generated" / "logs"
    tpath = logs_dir / "transcript.jsonl"
    fpath = logs_dir / "transcript_full.jsonl"
    records = _read_jsonl(fpath if fpath.is_file() else tpath)
    if not records and tpath.is_file():
        records = _read_jsonl(tpath)

    known_convs = {c["id"] for c in list_conversations(root)}

    title = conv_id[:8]
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    prev_step_ts: datetime | None = None
    active_ms = 0

    models_used: Counter[str] = Counter()
    tool_counts: Counter[str] = Counter()
    skill_loads: Counter[str] = Counter()
    program_counts: Counter[str] = Counter()

    total_input = 0
    total_cache = 0
    total_output = 0
    total_cost = 0.0
    turns = 0
    compactions = 0
    errors = 0

    # Composition accumulators (estimated tokens across conversation)
    comp_totals = {
        "system": 0,
        "tools": 0,
        "user": 0,
        "injected": 0,
        "skills": 0,
        "assistant": 0,
        "tool_results": 0,
        "output": 0,
    }

    steps: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    file_ops: dict[str, dict[str, Any]] = {}
    commands: list[dict[str, Any]] = []
    subagents: list[dict[str, Any]] = []
    agent_messages: list[dict[str, Any]] = []

    pending_user = ""
    pending_injected: list[str] = []
    pending_tool_results: list[dict[str, Any]] = []
    last_model: str | None = None
    prev_context_total = 0
    base_system_tokens = 0

    # Track running cumulative estimated tokens per category for current context bar
    cum_est = {
        "system_tools": 0,
        "user": 0,
        "skills": 0,
        "injected": 0,
        "assistant": 0,
        "tool_results": 0,
    }

    last_message = ""
    workspace = "Antigravity"

    for idx, rec in enumerate(records):
        rtype = rec.get("type", "")
        ts = parse_ts(rec.get("created_at"))
        if ts:
            if first_ts is None:
                first_ts = ts
            last_ts = ts
            if prev_step_ts is not None:
                diff = (ts - prev_step_ts).total_seconds()
                if 0 <= diff <= 120:
                    active_ms += int(diff * 1000)
            prev_step_ts = ts

        step_idx = rec.get("step_index", idx)

        if rtype == "USER_INPUT":
            turns += 1
            cleaned = clean_user_text(rec.get("content", ""))
            if turns == 1 and cleaned:
                title = cleaned.splitlines()[0][:80]
            if cleaned:
                last_message = cleaned.splitlines()[0][:100]
            pending_user = cleaned
            u_tok = estimate_tokens(cleaned)
            comp_totals["user"] += u_tok
            cum_est["user"] += u_tok

        elif rtype == "CHECKPOINT":
            compactions += 1
            content = rec.get("content", "")
            events.append(
                {
                    "kind": "compaction",
                    "step": step_idx,
                    "turn": turns,
                    "ts": rec.get("created_at"),
                    "title": "Context Compaction",
                    "detail": content[:160],
                    "delta": -max(0, prev_context_total - (rec.get("input_tokens", 0) + rec.get("cache_read_tokens", 0))),
                }
            )
            pending_injected.append(content[:200])
            inj_tok = estimate_tokens(content)
            comp_totals["injected"] += inj_tok
            cum_est["injected"] = inj_tok
            cum_est["user"] = 0
            cum_est["assistant"] = 0
            cum_est["tool_results"] = 0

        elif rtype == "SYSTEM_MESSAGE":
            content = rec.get("content", "")
            pending_injected.append(content[:200])
            inj_tok = estimate_tokens(content)
            comp_totals["injected"] += inj_tok
            cum_est["injected"] += inj_tok
            events.append(
                {
                    "kind": "system",
                    "step": step_idx,
                    "turn": turns,
                    "ts": rec.get("created_at"),
                    "title": "System Notification",
                    "detail": content[:180],
                }
            )
            # Check if a subagent ID is mentioned
            for m in UUID_RE.findall(content):
                if m in known_convs and m != conv_id:
                    agent_messages.append(
                        {
                            "step": step_idx,
                            "ts": rec.get("created_at"),
                            "from": m,
                            "to": conv_id,
                            "kind": "system_notify",
                            "summary": content[:160],
                        }
                    )

        elif rtype == "ERROR_MESSAGE":
            errors += 1
            content = rec.get("content", "")
            events.append(
                {
                    "kind": "error",
                    "step": step_idx,
                    "turn": turns,
                    "ts": rec.get("created_at"),
                    "title": "Error",
                    "detail": content[:180],
                }
            )

        elif rtype == "GENERIC":
            content = rec.get("content", "")
            tok = estimate_tokens(content)
            comp_totals["tool_results"] += tok
            cum_est["tool_results"] += tok
            pending_tool_results.append({"step": step_idx, "chars": len(content), "tokens": tok, "preview": content[:140]})

            # Check for command exit code on most recent command
            if commands and commands[-1].get("exit_code") is None:
                m_exit = EXIT_CODE_RE.search(content)
                if m_exit:
                    code = int(m_exit.group(1))
                    commands[-1]["exit_code"] = code
                    commands[-1]["status"] = "ok" if code == 0 else "error"
                elif "running as a background task" in content:
                    commands[-1]["status"] = "async"

            # Check for subagent conversationId in invoke_subagent result
            for m in UUID_RE.findall(content):
                if m in known_convs and m != conv_id:
                    # Link to latest unlinked subagent
                    for sa in reversed(subagents):
                        if not sa.get("child_id"):
                            sa["child_id"] = m
                            break

        elif rtype == "PLANNER_RESPONSE":
            model = rec.get("model") or "unknown"
            models_used[model] += 1
            in_tok = int(rec.get("input_tokens") or 0)
            cache_tok = int(rec.get("cache_read_tokens") or 0)
            out_tok = int(rec.get("output_tokens") or 0)
            ctx_total = in_tok + cache_tok

            total_input += in_tok
            total_cache += cache_tok
            total_output += out_tok
            step_cost = estimate_cost(model, in_tok, cache_tok, out_tok)
            total_cost += step_cost

            if len(steps) == 0:
                base_system_tokens = max(0, ctx_total - cum_est["user"])
                sys_part = int(base_system_tokens * 0.25)
                tool_part = base_system_tokens - sys_part
                comp_totals["system"] += sys_part
                comp_totals["tools"] += tool_part
                cum_est["system_tools"] = base_system_tokens

            flags: list[str] = []
            if last_model and model != last_model:
                flags.append("model_switch")
                events.append(
                    {
                        "kind": "model_switch",
                        "step": step_idx,
                        "turn": turns,
                        "ts": rec.get("created_at"),
                        "title": f"Model Switch: {last_model} -> {model}",
                        "detail": f"Context at {ctx_total:,} tokens",
                    }
                )
            last_model = model

            if compactions > 0 and ctx_total < prev_context_total * 0.75:
                flags.append("compaction")

            thinking = rec.get("thinking") or ""
            answer = rec.get("content") or ""
            t_calls = rec.get("tool_calls") or []

            th_tok = estimate_tokens(thinking)
            ans_tok = estimate_tokens(answer)
            tc_tok = estimate_tokens(t_calls)

            comp_totals["assistant"] += ans_tok + tc_tok
            comp_totals["output"] += out_tok
            cum_est["assistant"] += th_tok + ans_tok + tc_tok

            call_names: list[str] = []
            for tc in t_calls:
                tname = tc.get("name") or "unknown"
                call_names.append(tname)
                tool_counts[tname] += 1
                args = parse_args(tc.get("args"))

                if tname == "read_file":
                    fpath_arg = str(args.get("Path") or "")
                    if fpath_arg:
                        rec_f = file_ops.setdefault(
                            fpath_arg,
                            {"path": fpath_arg, "read": 0, "written": 0, "edited": 0, "added_lines": 0, "removed_lines": 0, "ops": []},
                        )
                        rec_f["read"] += 1
                        rec_f["ops"].append({"op": "read", "step": step_idx, "ts": rec.get("created_at")})
                        if fpath_arg.lower().endswith("skill.md"):
                            skill_name = Path(fpath_arg).parent.name
                            skill_loads[skill_name] += 1
                            flags.append("skill_load")
                            sk_tok = 800
                            comp_totals["skills"] += sk_tok
                            cum_est["skills"] += sk_tok
                            events.append(
                                {
                                    "kind": "skill",
                                    "step": step_idx,
                                    "turn": turns,
                                    "ts": rec.get("created_at"),
                                    "title": f"Skill Loaded: {skill_name}",
                                    "detail": fpath_arg,
                                }
                            )

                elif tname == "write_file":
                    fpath_arg = str(args.get("Path") or "")
                    content_arg = str(args.get("Content") or "")
                    lines_n = len(content_arg.splitlines()) if content_arg else 0
                    if fpath_arg:
                        rec_f = file_ops.setdefault(
                            fpath_arg,
                            {"path": fpath_arg, "read": 0, "written": 0, "edited": 0, "added_lines": 0, "removed_lines": 0, "ops": []},
                        )
                        rec_f["written"] += 1
                        rec_f["added_lines"] += lines_n
                        rec_f["ops"].append({"op": "write", "step": step_idx, "lines": f"+{lines_n}", "ts": rec.get("created_at")})

                elif tname == "edit_file":
                    fpath_arg = str(args.get("Path") or "")
                    target_s = str(args.get("Target") or "")
                    repl_s = str(args.get("Replacement") or "")
                    add_n = len(repl_s.splitlines()) if repl_s else 0
                    rem_n = len(target_s.splitlines()) if target_s else 0
                    if fpath_arg:
                        rec_f = file_ops.setdefault(
                            fpath_arg,
                            {"path": fpath_arg, "read": 0, "written": 0, "edited": 0, "added_lines": 0, "removed_lines": 0, "ops": []},
                        )
                        rec_f["edited"] += 1
                        rec_f["added_lines"] += add_n
                        rec_f["removed_lines"] += rem_n
                        rec_f["ops"].append({"op": "edit", "step": step_idx, "lines": f"+{add_n}/-{rem_n}", "ts": rec.get("created_at")})

                elif tname == "run_command":
                    cmd_str = str(args.get("CommandLine") or "")
                    prog = _extract_command_program(cmd_str)
                    program_counts[prog] += 1
                    commands.append(
                        {
                            "step": step_idx,
                            "ts": rec.get("created_at"),
                            "program": prog,
                            "command": cmd_str[:300],
                            "summary": str(args.get("toolSummary") or ""),
                            "exit_code": None,
                            "status": "unknown",
                        }
                    )

                elif tname == "invoke_subagent":
                    flags.append("subagent")
                    role = str(args.get("Role") or args.get("TypeName") or "Subagent")
                    sa_type = str(args.get("TypeName") or "subagent")
                    sa_model = str(args.get("Model") or "inherit")
                    prompt = str(args.get("Prompt") or "")
                    subagents.append(
                        {
                            "step": step_idx,
                            "ts": rec.get("created_at"),
                            "role": role,
                            "type": sa_type,
                            "model": sa_model,
                            "prompt": prompt[:240],
                            "child_id": None,
                        }
                    )
                    events.append(
                        {
                            "kind": "subagent",
                            "step": step_idx,
                            "turn": turns,
                            "ts": rec.get("created_at"),
                            "title": f"Spawned Subagent: {role}",
                            "detail": f"Type={sa_type}, Model={sa_model}",
                        }
                    )

                elif tname == "send_message":
                    flags.append("subagent")
                    recip = str(args.get("Recipient") or "")
                    msg_s = str(args.get("Message") or "")
                    agent_messages.append(
                        {
                            "step": step_idx,
                            "ts": rec.get("created_at"),
                            "from": conv_id,
                            "to": recip,
                            "kind": "send_message",
                            "summary": msg_s[:160],
                        }
                    )

            if rec.get("content") and not last_message:
                last_message = str(rec.get("content"))[:100]
            delta = ctx_total - prev_context_total if steps else ctx_total
            prev_context_total = ctx_total

            # Normalize cumulative breakdown to match actual ctx_total
            est_sum = sum(cum_est.values()) or 1
            norm_comp = {k: int(round(ctx_total * (v / est_sum))) for k, v in cum_est.items()}

            steps.append(
                {
                    "step": step_idx,
                    "turn": turns,
                    "ts": rec.get("created_at"),
                    "model": model,
                    "input_tokens": in_tok,
                    "cache_read_tokens": cache_tok,
                    "output_tokens": out_tok,
                    "context_total": ctx_total,
                    "delta": delta,
                    "cost": round(step_cost, 6),
                    "flags": sorted(set(flags)),
                    "composition": norm_comp,
                    "brief": {
                        "user": pending_user[:140] if pending_user else "",
                        "in_count": len(pending_tool_results) + len(pending_injected),
                        "tools": call_names,
                        "answer": answer[:160] if answer else "",
                    },
                }
            )
            pending_user = ""
            pending_injected = []
            pending_tool_results = []

    # Mark peak delta step
    if len(steps) > 1:
        peak_step = max(steps[1:], key=lambda s: s["delta"])
        if peak_step["delta"] > 0 and "peak_delta" not in peak_step["flags"]:
            peak_step["flags"].append("peak_delta")

    latest_step = steps[-1] if steps else None
    primary_model = models_used.most_common(1)[0][0] if models_used else "unknown"
    window_size = get_model_info(primary_model)["window"]
    current_ctx = latest_step["context_total"] if latest_step else 0
    total_billed = total_input + total_cache + total_output
    prompt_total = total_input + total_cache
    cache_rate = round((total_cache / prompt_total) * 100, 1) if prompt_total > 0 else 0.0

    # Composition summary scaled to total_billed
    est_comp_sum = sum(comp_totals.values()) or 1
    billed_comp = {k: int(round(total_billed * (v / est_comp_sum))) for k, v in comp_totals.items()}

    wall_ms = int((last_ts - first_ts).total_seconds() * 1000) if (first_ts and last_ts) else 0

    for fp in file_ops:
        norm = fp.replace("\\", "/")
        if "/GeminiWorkplace" in norm:
            workspace = "GeminiWorkplace"
            break
        if "/.gemini/" not in norm and "/Desktop/" in norm:
            parts = norm.split("/Desktop/", 1)[1].split("/")
            if parts and parts[0]:
                workspace = parts[0]
                break

    return {
        "id": conv_id,
        "title": title,
        "last_message": last_message,
        "workspace": workspace,
        "primary_model": primary_model,
        "models": dict(models_used),
        "window_size": window_size,
        "first_ts": first_ts.isoformat() if first_ts else None,
        "last_ts": last_ts.isoformat() if last_ts else None,
        "stats": {
            "turns": turns,
            "steps": len(steps),
            "tool_calls": sum(tool_counts.values()),
            "subagents": len(subagents),
            "compactions": compactions,
            "errors": errors,
            "current_context": current_ctx,
            "context_pct": round((current_ctx / window_size) * 100, 1) if window_size else 0.0,
            "total_input": total_input,
            "total_cache": total_cache,
            "total_output": total_output,
            "total_billed": total_billed,
            "cache_hit_pct": cache_rate,
            "estimated_cost": round(total_cost, 4),
            "wall_ms": wall_ms,
            "active_ms": active_ms,
        },
        "current_composition": latest_step["composition"] if latest_step else cum_est,
        "billed_composition": billed_comp,
        "steps": steps,
        "events": events,
        "file_activity": sorted(file_ops.values(), key=lambda x: x["read"] + x["written"] + x["edited"], reverse=True),
        "commands": commands,
        "programs": dict(program_counts.most_common(20)),
        "tools": dict(tool_counts.most_common(25)),
        "skills": dict(skill_loads.most_common(20)),
        "subagents": subagents,
        "agent_messages": agent_messages,
    }


def get_overview(days: int = 7, brain_dir: Path | None = None) -> dict[str, Any]:
    root = brain_dir or get_brain_dir()
    convs = list_conversations(root)
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)) if days > 0 else datetime.min.replace(tzinfo=timezone.utc)
    heatmap_cutoff = now - timedelta(days=56)

    daily: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"input": 0, "cache": 0, "output": 0, "billed": 0, "cost": 0.0, "sessions": set()}
    )
    heatmap: dict[str, dict[str, Any]] = defaultdict(lambda: {"billed": 0, "sessions": set()})

    agg_tools: Counter[str] = Counter()
    agg_programs: Counter[str] = Counter()
    agg_skills: Counter[str] = Counter()
    skill_meta: dict[str, dict[str, Any]] = {}
    agg_comp: Counter[str] = Counter()

    kpi_billed = 0
    kpi_input = 0
    kpi_cache = 0
    kpi_output = 0
    kpi_cost = 0.0
    kpi_turns = 0
    kpi_steps = 0
    kpi_tools = 0
    kpi_compactions = 0
    kpi_active_ms = 0
    active_sessions = 0

    session_cards: list[dict[str, Any]] = []

    for c in convs:
        data = analyze_conversation(c["id"], root)
        st = data["stats"]
        last_dt = parse_ts(data["last_ts"])
        in_range = True if days <= 0 else bool(last_dt and last_dt >= cutoff)

        if in_range:
            active_sessions += 1
            kpi_billed += st["total_billed"]
            kpi_input += st["total_input"]
            kpi_cache += st["total_cache"]
            kpi_output += st["total_output"]
            kpi_cost += st["estimated_cost"]
            kpi_turns += st["turns"]
            kpi_steps += st["steps"]
            kpi_tools += st["tool_calls"]
            kpi_compactions += st["compactions"]
            kpi_active_ms += st["active_ms"]

            for k, v in data["billed_composition"].items():
                agg_comp[k] += v
            for k, v in data["tools"].items():
                agg_tools[k] += v
            for k, v in data["programs"].items():
                agg_programs[k] += v
            for k, v in data["skills"].items():
                agg_skills[k] += v
                sm = skill_meta.setdefault(k, {"sessions": 0, "last_ts": ""})
                sm["sessions"] += 1
                if (data["last_ts"] or "") > sm["last_ts"]:
                    sm["last_ts"] = data["last_ts"] or ""

        active_dates: set[str] = set()
        for step in data["steps"]:
            sdt = parse_ts(step.get("ts"))
            if not sdt:
                continue
            dstr = sdt.astimezone().strftime("%Y-%m-%d")
            active_dates.add(dstr)
            sbilled = step["input_tokens"] + step["cache_read_tokens"] + step["output_tokens"]
            if sdt >= heatmap_cutoff:
                heatmap[dstr]["billed"] += sbilled
                heatmap[dstr]["sessions"].add(c["id"])
            if sdt >= cutoff:
                daily[dstr]["input"] += step["input_tokens"]
                daily[dstr]["cache"] += step["cache_read_tokens"]
                daily[dstr]["output"] += step["output_tokens"]
                daily[dstr]["billed"] += sbilled
                daily[dstr]["cost"] += step["cost"]
                daily[dstr]["sessions"].add(c["id"])

        session_cards.append(
            {
                "id": data["id"],
                "title": data["title"],
                "last_message": data["last_message"],
                "workspace": data["workspace"],
                "primary_model": data["primary_model"],
                "last_ts": data["last_ts"],
                "active_dates": sorted(active_dates),
                "stats": st,
                "current_composition": data["current_composition"],
                "tools": list(data["tools"].keys())[:10],
                "skills": list(data["skills"].keys()),
                "programs": list(data["programs"].keys())[:8],
            }
        )

    prompt_tot = kpi_input + kpi_cache
    kpi_cache_pct = round((kpi_cache / prompt_tot) * 100, 1) if prompt_tot > 0 else 0.0

    # Build ordered daily series for chart (always show 7 days for the 7d chart)
    chart_days = 7 if days <= 0 or days == 7 else min(days, 30)
    daily_series = []
    for i in range(chart_days - 1, -1, -1):
        dstr = (now - timedelta(days=i)).astimezone().strftime("%Y-%m-%d")
        row = daily.get(dstr)
        daily_series.append(
            {
                "date": dstr,
                "input": row["input"] if row else 0,
                "cache": row["cache"] if row else 0,
                "output": row["output"] if row else 0,
                "billed": row["billed"] if row else 0,
                "cost": round(row["cost"], 4) if row else 0.0,
                "sessions": len(row["sessions"]) if row else 0,
            }
        )

    heatmap_series = []
    for i in range(55, -1, -1):
        dstr = (now - timedelta(days=i)).astimezone().strftime("%Y-%m-%d")
        row = heatmap.get(dstr)
        heatmap_series.append(
            {
                "date": dstr,
                "billed": row["billed"] if row else 0,
                "sessions": len(row["sessions"]) if row else 0,
            }
        )

    return {
        "days": days,
        "total_sessions": len(convs),
        "kpis": {
            "active_sessions": active_sessions,
            "total_billed": kpi_billed,
            "total_output": kpi_output,
            "estimated_cost": round(kpi_cost, 4),
            "cache_hit_pct": kpi_cache_pct,
            "turns": kpi_turns,
            "steps": kpi_steps,
            "tool_calls": kpi_tools,
            "compactions": kpi_compactions,
            "active_ms": kpi_active_ms,
        },
        "daily": daily_series,
        "heatmap": heatmap_series,
        "composition": dict(agg_comp),
        "top_tools": [{"name": k, "count": v, "source": _tool_source_tag(k)} for k, v in agg_tools.most_common(12)],
        "top_programs": [{"name": k, "count": v} for k, v in agg_programs.most_common(10)],
        "top_skills": [
            {"name": k, "count": v, "sessions": skill_meta.get(k, {}).get("sessions", 1), "last_ts": skill_meta.get(k, {}).get("last_ts", "")}
            for k, v in agg_skills.most_common(12)
        ],
        "sessions": session_cards,
    }


def get_step_detail(conv_id: str, step_index: int, brain_dir: Path | None = None) -> dict[str, Any]:
    """Return full, untruncated step breakdown for Context Browser and DNA view."""
    root = brain_dir or get_brain_dir()
    logs_dir = root / conv_id / ".system_generated" / "logs"
    fpath = logs_dir / "transcript_full.jsonl"
    tpath = logs_dir / "transcript.jsonl"
    records = _read_jsonl(fpath if fpath.is_file() else tpath)

    items: list[dict[str, Any]] = []
    target_step: dict[str, Any] | None = None
    pos = 1

    last_message = ""
    workspace = "Antigravity"

    for idx, rec in enumerate(records):
        s_idx = rec.get("step_index", idx)
        if s_idx > step_index:
            break
        rtype = rec.get("type", "")
        if rtype == "USER_INPUT":
            txt = clean_user_text(rec.get("content", ""))
            items.append({"pos": pos, "category": "user", "step": s_idx, "tokens": estimate_tokens(txt), "title": "User Input", "content": txt})
            pos += 1
        elif rtype == "CHECKPOINT":
            txt = rec.get("content", "")
            items = [i for i in items if i["category"] == "system_tools"]
            items.append({"pos": pos, "category": "injected", "step": s_idx, "tokens": estimate_tokens(txt), "title": "Compaction Checkpoint", "content": txt})
            pos += 1
        elif rtype == "SYSTEM_MESSAGE":
            txt = rec.get("content", "")
            items.append({"pos": pos, "category": "injected", "step": s_idx, "tokens": estimate_tokens(txt), "title": "System Message", "content": txt})
            pos += 1
        elif rtype == "GENERIC":
            txt = rec.get("content", "")
            items.append({"pos": pos, "category": "tool_results", "step": s_idx, "tokens": estimate_tokens(txt), "title": "Tool Result", "content": txt})
            pos += 1
        elif rtype == "PLANNER_RESPONSE":
            if rec.get("thinking"):
                th = rec["thinking"]
                items.append({"pos": pos, "category": "thinking", "step": s_idx, "tokens": estimate_tokens(th), "title": "Reasoning", "content": th})
                pos += 1
            for tc in rec.get("tool_calls") or []:
                tname = tc.get("name", "tool")
                targs = json.dumps(parse_args(tc.get("args")), ensure_ascii=False, indent=2)
                items.append({"pos": pos, "category": "tool_calls", "step": s_idx, "tokens": estimate_tokens(targs), "title": f"Tool Call: {tname}", "source": _tool_source_tag(tname), "content": targs})
                pos += 1
            if rec.get("content"):
                ans = rec["content"]
                items.append({"pos": pos, "category": "answer", "step": s_idx, "tokens": estimate_tokens(ans), "title": "Assistant Answer", "content": ans})
                pos += 1
            if s_idx == step_index:
                target_step = rec

    # Trim huge item contents to keep response safe under 800KB
    for it in items:
        if len(it["content"]) > 12000:
            it["content"] = it["content"][:12000] + "\n... [truncated for viewer]"

    return {
        "conversation_id": conv_id,
        "step_index": step_index,
        "model": target_step.get("model") if target_step else None,
        "input_tokens": target_step.get("input_tokens", 0) if target_step else 0,
        "cache_read_tokens": target_step.get("cache_read_tokens", 0) if target_step else 0,
        "output_tokens": target_step.get("output_tokens", 0) if target_step else 0,
        "items": list(reversed(items)),  # DNA view: newest first
    }


def get_fleet(conv_id: str | None = None, brain_dir: Path | None = None) -> dict[str, Any]:
    root = brain_dir or get_brain_dir()
    convs = list_conversations(root)
    if not convs:
        return {"root_id": None, "nodes": [], "edges": [], "timeline": []}

    target_id = conv_id or convs[0]["id"]

    # Build parent lookup across conversations
    parent_map: dict[str, str] = {}
    children_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cache_analysis: dict[str, dict[str, Any]] = {}

    for c in convs[:40]:
        d = analyze_conversation(c["id"], root)
        cache_analysis[c["id"]] = d
        for sa in d["subagents"]:
            cid = sa.get("child_id")
            if cid:
                parent_map[cid] = c["id"]
                children_map[c["id"]].append(sa)

    # Walk up to root of this agent family
    root_id = target_id
    visited = {root_id}
    while root_id in parent_map and parent_map[root_id] not in visited:
        root_id = parent_map[root_id]
        visited.add(root_id)

    # BFS down to collect family
    family_ids: list[str] = []
    queue = [root_id]
    seen = set()
    while queue:
        curr = queue.pop(0)
        if curr in seen:
            continue
        seen.add(curr)
        family_ids.append(curr)
        for sa in children_map.get(curr, []):
            if sa.get("child_id"):
                queue.append(sa["child_id"])

    nodes = []
    edges = []
    timeline = []

    for fid in family_ids:
        d = cache_analysis.get(fid) or analyze_conversation(fid, root)
        parent_id = parent_map.get(fid)
        role_label = "Coordinator" if fid == root_id else "Subagent"
        if parent_id:
            for sa in children_map.get(parent_id, []):
                if sa.get("child_id") == fid:
                    role_label = sa.get("role") or role_label
                    break

        nodes.append(
            {
                "id": fid,
                "parent_id": parent_id,
                "is_current": fid == target_id,
                "role": role_label,
                "title": d["title"],
                "model": d["primary_model"],
                "stats": d["stats"],
                "current_composition": d["current_composition"],
            }
        )

        if parent_id:
            edges.append({"from": parent_id, "to": fid, "role": role_label})

        for sa in d["subagents"]:
            timeline.append(
                {
                    "ts": sa["ts"],
                    "agent_id": fid,
                    "kind": "delegate",
                    "target": sa.get("child_id") or sa["role"],
                    "summary": f"Delegated [{sa['role']}] ({sa['type']}): {sa['prompt'][:100]}",
                }
            )
        for msg in d["agent_messages"]:
            timeline.append(
                {
                    "ts": msg["ts"],
                    "agent_id": fid,
                    "kind": msg["kind"],
                    "target": msg["to"],
                    "summary": msg["summary"],
                }
            )
        for ev in d["events"]:
            if ev["kind"] in ("compaction", "error", "model_switch"):
                timeline.append(
                    {
                        "ts": ev["ts"],
                        "agent_id": fid,
                        "kind": ev["kind"],
                        "target": fid,
                        "summary": f"{ev['title']}: {ev['detail'][:100]}",
                    }
                )

    timeline.sort(key=lambda x: x.get("ts") or "")

    return {
        "root_id": root_id,
        "current_id": target_id,
        "nodes": nodes,
        "edges": edges,
        "timeline": timeline,
    }
