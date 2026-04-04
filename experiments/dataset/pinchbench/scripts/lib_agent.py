"""
OpenClaw agent execution helpers for PinchBench.
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import subprocess
import time
import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Tuple

from lib_tasks import Task


logger = logging.getLogger(__name__)
MAX_OPENCLAW_MESSAGE_CHARS = int(os.environ.get("PINCHBENCH_MAX_MSG_CHARS", "32000"))
CONTEXT_HASH_PREFIX_CHARS = int(os.environ.get("PINCHBENCH_CONTEXT_HASH_PREFIX_CHARS", "1024"))
CONTEXT_RECENT_MESSAGES = int(os.environ.get("PINCHBENCH_CONTEXT_RECENT_MESSAGES", "4"))
OPENCLAW_AGENT_LOCK_FILE = Path(
    os.environ.get("PINCHBENCH_OPENCLAW_AGENT_LOCK_FILE", "/tmp/pinchbench_openclaw_agents.lock")
)


@contextmanager
def _openclaw_agent_lock() -> Any:
    OPENCLAW_AGENT_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OPENCLAW_AGENT_LOCK_FILE.open("a+") as lock_fp:
        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)


def slugify_model(model_id: str) -> str:
    return model_id.replace("/", "-").replace(".", "-").lower()


def _ensure_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _message_content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, bytes):
        return content.decode("utf-8", errors="replace")
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                # Keep this generic: capture common payload fields without
                # assuming one provider schema.
                item_type = item.get("type")
                if item_type:
                    parts.append(f"[{item_type}]")
                for key in ("text", "content", "input", "output", "result", "value"):
                    if key in item:
                        parts.append(_message_content_to_text(item.get(key)))
            else:
                parts.append(_message_content_to_text(item))
        return "\n".join([p for p in parts if p])
    if isinstance(content, dict):
        try:
            return json.dumps(content, ensure_ascii=False, sort_keys=True)
        except TypeError:
            return str(content)
    return str(content)


def _normalize_cache_signature_text(text: str) -> str:
    normalized = text
    normalized = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", "<UUID>", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"/tmp/pinchbench/[^\s\"']+", "/tmp/pinchbench/<PATH>", normalized)
    normalized = re.sub(r"\b\d{4}-\d{2}-\d{2}[T ][0-9:\.\+\-Z]{6,}\b", "<TIMESTAMP>", normalized)
    normalized = re.sub(r"\b\d{10,}\b", "<LONGNUM>", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _build_call_context_detail(
    transcript: List[Dict[str, Any]],
    assistant_entry_index: int,
) -> Dict[str, Any]:
    message_items: List[Dict[str, Any]] = []
    message_indices: List[int] = []
    for idx, entry in enumerate(transcript[:assistant_entry_index]):
        if entry.get("type") != "message":
            continue
        msg = entry.get("message", {}) if isinstance(entry.get("message"), dict) else {}
        role = str(msg.get("role") or "unknown")
        content_text = _message_content_to_text(msg.get("content"))
        message_items.append(
            {
                "transcript_index": idx,
                "role": role,
                "content": content_text,
            }
        )
        message_indices.append(idx)

    signature_payload = json.dumps(message_items, ensure_ascii=False, sort_keys=True)
    normalized_payload = _normalize_cache_signature_text(signature_payload)
    prefix_chars = max(128, CONTEXT_HASH_PREFIX_CHARS)
    recent_count = max(1, CONTEXT_RECENT_MESSAGES)
    recent_messages = message_items[-recent_count:]

    return {
        "assistant_transcript_index": assistant_entry_index,
        "context_message_count": len(message_items),
        "context_message_indices": message_indices,
        "context_chars": len(signature_payload),
        "context_signature_sha256": _sha256_text(signature_payload),
        "context_signature_normalized_sha256": _sha256_text(normalized_payload),
        "prefix_chars": prefix_chars,
        "prefix_signature_sha256": _sha256_text(signature_payload[:prefix_chars]),
        "prefix_signature_normalized_sha256": _sha256_text(normalized_payload[:prefix_chars]),
        "recent_messages": recent_messages,
    }



def _get_agent_workspace(agent_id: str) -> Path | None:
    """Get the workspace path for an agent from OpenClaw config."""
    try:
        list_result = subprocess.run(
            ["openclaw", "agents", "list"],
            capture_output=True,
            text=True,
            check=False,
        )
        if list_result.returncode != 0:
            return None

        # Parse the agent list output to find workspace
        # OpenClaw normalizes colons to dashes in agent names, so check both.
        normalized_id = agent_id.replace(":", "-")
        lines = list_result.stdout.split("\n")
        found_agent = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(f"- {agent_id}") or stripped.startswith(f"- {normalized_id}"):
                found_agent = True
            elif found_agent and "Workspace:" in line:
                workspace_str = line.split("Workspace:")[1].strip()
                # Expand ~ if present
                if workspace_str.startswith("~/"):
                    workspace_str = str(Path.home() / workspace_str[2:])
                return Path(workspace_str)
            elif found_agent and line.strip().startswith("-"):
                # Found next agent, stop looking
                break
        return None
    except Exception as exc:
        logger.warning("Failed to get agent workspace: %s", exc)
        return None


def ensure_agent_exists(agent_id: str, model_id: str, workspace_dir: Path) -> bool:
    """Ensure the OpenClaw agent exists with the correct workspace.

    If the agent already exists but points to a different workspace, it is
    deleted and recreated so that the new workspace takes effect.
    Returns True if the agent was (re)created.
    """
    workspace_dir.mkdir(parents=True, exist_ok=True)

    with _openclaw_agent_lock():
        try:
            list_result = subprocess.run(
                ["openclaw", "agents", "list"],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            logger.error("openclaw CLI not found while listing agents")
            return False

        if list_result.returncode == 0:
            existing_agents = set()
            for line in list_result.stdout.splitlines():
                line = line.strip()
                if line.startswith("- "):
                    name_part = line[2:].split()[0] if line[2:].strip() else ""
                    if name_part:
                        existing_agents.add(name_part)
            normalized_id = agent_id.replace(":", "-")
            if agent_id in existing_agents or normalized_id in existing_agents:
                current_workspace = _get_agent_workspace(agent_id)
                if (
                    current_workspace is not None
                    and current_workspace.resolve() == workspace_dir.resolve()
                ):
                    logger.info("Agent %s already exists with correct workspace", agent_id)
                    return False
                delete_name = normalized_id if normalized_id in existing_agents else agent_id
                logger.info(
                    "Agent %s exists with stale workspace (%s != %s), recreating",
                    agent_id,
                    current_workspace,
                    workspace_dir,
                )
                subprocess.run(
                    ["openclaw", "agents", "delete", delete_name, "--force"],
                    capture_output=True,
                    text=True,
                    check=False,
                )

        logger.info("Creating OpenClaw agent %s", agent_id)
        try:
            create_result = subprocess.run(
                [
                    "openclaw",
                    "agents",
                    "add",
                    agent_id,
                    "--model",
                    model_id,
                    "--workspace",
                    str(workspace_dir),
                    "--non-interactive",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            logger.error("openclaw CLI not found while creating agent")
            return False

        if create_result.returncode != 0:
            logger.warning(
                "Agent creation returned %s: %s", create_result.returncode, create_result.stderr
            )
        return True


def cleanup_agent_sessions(agent_id: str) -> None:
    """Remove stored session transcripts for an agent to avoid unbounded growth."""
    agent_dir = _get_agent_store_dir(agent_id)
    sessions_dir = agent_dir / "sessions"
    if not sessions_dir.exists():
        return
    removed = 0
    for pattern in ("*.jsonl", "*.jsonl.lock"):
        for path in sessions_dir.glob(pattern):
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                logger.warning("Failed to remove session file %s: %s", path, exc)
    sessions_store = sessions_dir / "sessions.json"
    if sessions_store.exists():
        try:
            sessions_store.unlink()
        except OSError as exc:
            logger.warning("Failed to remove session store %s: %s", sessions_store, exc)
    if removed:
        logger.info("Removed %s old OpenClaw session transcripts for %s", removed, agent_id)


def prepare_task_workspace(
    skill_dir: Path,
    run_id: str,
    task: Task,
    agent_id: str,
    workspace_override: Path | None = None,
) -> Path:
    """
    Prepare workspace for a task by copying fixtures.
    Uses the agent's configured workspace to ensure files are in the right place.
    """
    import shutil

    # Prefer explicit workspace from caller (parallel-safe).
    workspace = workspace_override
    if workspace is None:
        # Get agent's workspace from agent config
        workspace = _get_agent_workspace(agent_id)
    if workspace is None:
        # Fallback to task-specific workspace if agent workspace not found
        logger.warning("Could not find agent workspace, using fallback")
        workspace = Path(f"/tmp/pinchbench/{run_id}/{task.task_id}")

    # Clear workspace before each task to prevent stale files from prior tasks
    # from contaminating the agent's context.
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    for file_spec in task.workspace_files:
        if "content" in file_spec:
            dest = workspace / file_spec["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(file_spec["content"])
            continue

        source = skill_dir / "assets" / file_spec["source"]
        dest = workspace / file_spec["dest"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            dest.write_bytes(source.read_bytes())
        except FileNotFoundError:
            logger.error("Workspace file not found: %s", source)
            raise

    return workspace


def _get_agent_store_dir(agent_id: str) -> Path:
    state_dir = os.environ.get("OPENCLAW_STATE_DIR")
    if state_dir:
        base_dir = Path(state_dir) / "agents"
    else:
        base_dir = Path.home() / ".openclaw" / "agents"
    direct_dir = base_dir / agent_id
    if direct_dir.exists():
        return direct_dir
    normalized_dir = base_dir / agent_id.replace(":", "-")
    if normalized_dir.exists():
        return normalized_dir
    return direct_dir


def _resolve_session_id_from_store(agent_id: str) -> str | None:
    agent_dir = _get_agent_store_dir(agent_id)
    sessions_store = agent_dir / "sessions" / "sessions.json"
    if not sessions_store.exists():
        return None
    try:
        sessions_payload = json.loads(sessions_store.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse sessions store: %s", exc)
        return None
    if not isinstance(sessions_payload, dict):
        return None

    normalized_id = agent_id.replace(":", "-")
    preferred_keys = [
        f"agent:{agent_id}:main",
        f"agent:{agent_id}:default",
        f"agent:{normalized_id}:main",
        f"agent:{normalized_id}:default",
    ]
    for key in preferred_keys:
        entry = sessions_payload.get(key)
        if isinstance(entry, dict) and entry.get("sessionId"):
            return entry["sessionId"]

    newest_entry = None
    newest_timestamp = -1
    for entry in sessions_payload.values():
        if not isinstance(entry, dict):
            continue
        if "sessionId" not in entry:
            continue
        updated_at = entry.get("updatedAt")
        if isinstance(updated_at, (int, float)) and updated_at > newest_timestamp:
            newest_timestamp = updated_at
            newest_entry = entry
    if newest_entry:
        return newest_entry.get("sessionId")
    return None


def _find_recent_session_path(agent_dir: Path, started_at: float) -> Path | None:
    sessions_dir = agent_dir / "sessions"
    if not sessions_dir.exists():
        return None
    candidates = list(sessions_dir.glob("*.jsonl"))
    if not candidates:
        return None
    tolerance_seconds = 5.0
    recent_candidates = [
        path for path in candidates if path.stat().st_mtime >= (started_at - tolerance_seconds)
    ]
    pool = recent_candidates or candidates
    return max(pool, key=lambda path: path.stat().st_mtime)


def _load_transcript(agent_id: str, session_id: str, started_at: float) -> List[Dict[str, Any]]:
    agent_dir = _get_agent_store_dir(agent_id)
    transcript_path = None

    # OpenClaw ignores the --session-id we pass and generates its own UUID-based
    # session ID internally.  We need to discover the actual transcript path.
    #
    # Strategy (with retries to handle write-delay):
    #   1. Resolve the real session ID from sessions.json
    #   2. Glob for any .jsonl in the sessions dir (most-recently-modified)
    #   3. Try our passed-in session ID as a last resort
    for attempt in range(6):
        # 1. Try sessions.json first — OpenClaw writes the real UUID here
        resolved_session_id = _resolve_session_id_from_store(agent_id)
        if resolved_session_id:
            candidate = agent_dir / "sessions" / f"{resolved_session_id}.jsonl"
            if candidate.exists():
                transcript_path = candidate
                logger.info(
                    "Found transcript via sessions.json: %s (attempt %s)",
                    candidate.name,
                    attempt + 1,
                )
                break

        # 2. Glob fallback — pick the most recently modified .jsonl
        recent_path = _find_recent_session_path(agent_dir, started_at)
        if recent_path is not None:
            transcript_path = recent_path
            logger.info(
                "Found transcript via glob fallback: %s (attempt %s)",
                recent_path.name,
                attempt + 1,
            )
            break

        # 3. Try our passed-in session ID (unlikely to work, but check anyway)
        direct_path = agent_dir / "sessions" / f"{session_id}.jsonl"
        if direct_path.exists():
            transcript_path = direct_path
            logger.info(
                "Found transcript via passed session ID: %s (attempt %s)",
                direct_path.name,
                attempt + 1,
            )
            break

        if attempt < 5:
            time.sleep(1.0)

    if transcript_path is None:
        sessions_dir = agent_dir / "sessions"
        if sessions_dir.exists():
            all_files = list(sessions_dir.iterdir())
            logger.warning(
                "Transcript not found for agent %s. Sessions dir contents: %s",
                agent_id,
                [f.name for f in all_files],
            )
        else:
            logger.warning(
                "Transcript not found — sessions dir does not exist: %s",
                sessions_dir,
            )
        return []

    return _parse_jsonl_file(transcript_path)


def _parse_jsonl_file(path: Path) -> List[Dict[str, Any]]:
    """Parse a JSONL transcript file into a list of dicts."""
    entries: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse transcript line in %s: %s", path.name, exc)
            entries.append({"raw": line, "parse_error": str(exc)})
    return entries


def _extract_usage_from_transcript(transcript: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Sum token usage and cost from all assistant messages in transcript."""
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            if value is None:
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "cache_hit_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "request_count": 0,
        "usage_available_count": 0,
        "usage_missing_count": 0,
    }

    for entry in transcript:
        if entry.get("type") != "message":
            continue
        msg = entry.get("message", {})
        if msg.get("role") != "assistant":
            continue
        totals["request_count"] += 1
        usage = msg.get("usage", {}) if isinstance(msg.get("usage"), dict) else {}
        provider_raw = usage.get("providerRaw", {})
        if not isinstance(provider_raw, dict):
            provider_raw = {}

        input_tokens = _to_int(usage.get("input"), _to_int(usage.get("input_tokens"), _to_int(usage.get("prompt_tokens"), 0)))
        output_tokens = _to_int(usage.get("output"), _to_int(usage.get("output_tokens"), _to_int(usage.get("completion_tokens"), 0)))
        if input_tokens == 0:
            input_tokens = _to_int(provider_raw.get("input_tokens"), _to_int(provider_raw.get("prompt_tokens"), 0))
        if output_tokens == 0:
            output_tokens = _to_int(provider_raw.get("output_tokens"), _to_int(provider_raw.get("completion_tokens"), 0))

        # Cross-provider cache fields:
        # - OpenClaw transcript style: cacheRead/cacheWrite
        # - Anthropic style: cache_read_input_tokens/cache_creation_input_tokens
        # - OpenAI style: prompt_tokens_details.cached_tokens
        cached_tokens = _to_int(
            usage.get("cachedTokens"),
            _to_int(
                usage.get("cached_tokens"),
                _to_int((usage.get("prompt_tokens_details") or {}).get("cached_tokens"), 0),
            ),
        )
        cache_read_tokens = _to_int(
            usage.get("cacheRead"),
            _to_int(
                usage.get("cache_read_tokens"),
                _to_int(usage.get("cache_read_input_tokens"), cached_tokens),
            ),
        )
        cache_write_tokens = _to_int(
            usage.get("cacheWrite"),
            _to_int(usage.get("cache_write_tokens"), _to_int(usage.get("cache_creation_input_tokens"), 0)),
        )
        total_tokens = _to_int(
            usage.get("totalTokens"),
            _to_int(usage.get("total_tokens"), input_tokens + output_tokens),
        )
        if total_tokens == 0:
            total_tokens = _to_int(provider_raw.get("total_tokens"), input_tokens + output_tokens)

        totals["input_tokens"] += input_tokens
        totals["output_tokens"] += output_tokens
        totals["cache_read_tokens"] += cache_read_tokens
        totals["cache_write_tokens"] += cache_write_tokens
        totals["cache_hit_tokens"] += cache_read_tokens
        totals["total_tokens"] += total_tokens
        cost = usage.get("cost", {})
        totals["cost_usd"] += _to_float(cost.get("total"), _to_float(usage.get("cost_usd"), 0.0))
        if input_tokens > 0 or output_tokens > 0 or total_tokens > 0:
            totals["usage_available_count"] += 1
        else:
            totals["usage_missing_count"] += 1

    return totals


def _extract_llm_calls_from_transcript(transcript: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract per-assistant-message LLM call metadata for debugging and audit."""
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            if value is None:
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    calls: List[Dict[str, Any]] = []
    for idx, entry in enumerate(transcript):
        if entry.get("type") != "message":
            continue
        msg = entry.get("message", {})
        if msg.get("role") != "assistant":
            continue

        usage = msg.get("usage", {}) if isinstance(msg.get("usage"), dict) else {}
        cost_obj = usage.get("cost", {}) if isinstance(usage.get("cost"), dict) else {}
        context_detail = _build_call_context_detail(transcript, idx)
        calls.append({
            "index": idx,
            "timestamp": msg.get("timestamp") or entry.get("timestamp"),
            "provider": msg.get("provider"),
            "model": msg.get("model"),
            "api": msg.get("api"),
            "stop_reason": msg.get("stopReason"),
            "input_tokens": _to_int(usage.get("input"), _to_int(usage.get("input_tokens"), _to_int(usage.get("prompt_tokens"), 0))),
            "output_tokens": _to_int(usage.get("output"), _to_int(usage.get("output_tokens"), _to_int(usage.get("completion_tokens"), 0))),
            "cache_read_tokens": _to_int(usage.get("cacheRead"), _to_int(usage.get("cache_read_tokens"), _to_int(usage.get("cache_read_input_tokens"), 0))),
            "cache_write_tokens": _to_int(usage.get("cacheWrite"), _to_int(usage.get("cache_write_tokens"), _to_int(usage.get("cache_creation_input_tokens"), 0))),
            "total_tokens": _to_int(usage.get("totalTokens"), _to_int(usage.get("total_tokens"), 0)),
            "cost_usd": _to_float(cost_obj.get("total"), _to_float(usage.get("cost_usd"), 0.0)),
            "context_detail": context_detail,
        })
    return calls


def _latest_assistant_message(transcript: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    for entry in reversed(transcript):
        if entry.get("type") != "message":
            continue
        message = entry.get("message", {})
        if isinstance(message, dict) and message.get("role") == "assistant":
            return message
    return None


def _is_transient_provider_error(transcript: List[Dict[str, Any]]) -> tuple[bool, str]:
    assistant_message = _latest_assistant_message(transcript)
    if not assistant_message:
        return False, ""
    if assistant_message.get("stopReason") != "error":
        return False, ""
    error_message = _ensure_text(assistant_message.get("errorMessage")).lower()
    signatures = [
        "bad gateway",
        "502",
        "connection error",
        "temporarily unavailable",
        "help.openai.com",
        "rate limit",
        "timeout",
    ]
    if any(signature in error_message for signature in signatures):
        return True, error_message[:200]
    return False, ""


def execute_openclaw_task(
    *,
    task: Task,
    agent_id: str,
    model_id: str,
    run_id: str,
    timeout_multiplier: float,
    skill_dir: Path,
    agent_workspace: Path | None = None,
    verbose: bool = False,
    enable_multi_agent: bool = False,
    multi_agent_ids: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    logger.info("🤖 Agent [%s] starting task: %s", agent_id, task.task_id)
    logger.info("   Task: %s", task.name)
    logger.info("   Category: %s", task.category)
    if enable_multi_agent:
        logger.info("   Mode: multi-agent (coordinator + %d workers)", len(multi_agent_ids or {}) - 1)
    if verbose:
        logger.info(
            "   Prompt: %s", task.prompt[:500] + "..." if len(task.prompt) > 500 else task.prompt
        )

    # Clean up previous session transcripts so we can reliably find this task's
    # transcript (OpenClaw uses its own UUID-based naming, not our session ID).
    if enable_multi_agent and multi_agent_ids:
        cleanup_multi_agent_sessions(multi_agent_ids)
    else:
        cleanup_agent_sessions(agent_id)

    start_time = time.time()
    workspace = prepare_task_workspace(
        skill_dir=skill_dir,
        run_id=run_id,
        task=task,
        agent_id=agent_id,
        workspace_override=agent_workspace,
    )
    session_id = f"{task.task_id}_{int(time.time() * 1000)}"
    timeout_seconds = task.timeout_seconds * timeout_multiplier
    if enable_multi_agent:
        timeout_seconds *= MULTI_AGENT_TIMEOUT_MULTIPLIER
    stdout = ""
    stderr = ""
    exit_code = -1
    timed_out = False

    session_plan = _build_task_session_plan(task)

    def _run_once(
        current_session_id: str,
        current_timeout_seconds: float,
        current_prompt: str,
    ) -> tuple[str, str, int, bool]:
        run_stdout = ""
        run_stderr = ""
        run_exit_code = -1
        run_timed_out = False
        try:
            result = subprocess.run(
                [
                    "openclaw",
                    "agent",
                    "--agent",
                    agent_id,
                    "--session-id",
                    current_session_id,
                    "--message",
                    current_prompt,
                ],
                capture_output=True,
                text=True,
                cwd=str(workspace),
                timeout=current_timeout_seconds,
                check=False,
            )
            run_stdout = result.stdout
            run_stderr = result.stderr
            run_exit_code = result.returncode
        except subprocess.TimeoutExpired as exc:
            run_timed_out = True
            run_stdout = _ensure_text(exc.stdout)
            run_stderr = _ensure_text(exc.stderr)
        except FileNotFoundError as exc:
            run_stderr = f"openclaw command not found: {exc}"
        return run_stdout, run_stderr, run_exit_code, run_timed_out

    executed_session_ids: List[str] = []
    current_session_id = session_id
    for index, session_spec in enumerate(session_plan):
        prompt_text = str(session_spec.get("prompt") or task.prompt).strip() or task.prompt
        if enable_multi_agent and multi_agent_ids:
            prompt_text = _wrap_prompt_for_multi_agent(
                prompt_text,
                task,
                multi_agent_ids,
                timeout_seconds,
            )
        if index == 0 or session_spec.get("new_session"):
            current_session_id = f"{task.task_id}_s{index + 1}_{int(time.time() * 1000)}"
            executed_session_ids.append(current_session_id)
        run_stdout, run_stderr, run_exit_code, run_timed_out = _run_once(
            current_session_id,
            timeout_seconds,
            prompt_text,
        )
        stdout = f"{stdout}\n{run_stdout}".strip() if stdout else run_stdout
        stderr = f"{stderr}\n{run_stderr}".strip() if stderr else run_stderr
        exit_code = run_exit_code
        timed_out = run_timed_out
        if timed_out or (exit_code not in (0, -1) and "openclaw command not found" not in str(stderr)):
            break

    if enable_multi_agent and multi_agent_ids:
        # In multi-agent mode, wait a bit for subagent sessions to be written
        time.sleep(2.0)
        transcript, all_transcripts = _collect_all_session_transcripts(
            multi_agent_ids, start_time,
        )
    else:
        transcript = _load_transcripts_for_session_ids(agent_id, executed_session_ids, start_time)
        all_transcripts = None

    # Parallel runs occasionally race with transcript persistence. Retry once
    # to reduce false negatives when execution succeeded but transcript is empty.
    # (Skip retries in multi-agent mode — coordinator handles its own flow.)
    if (
        not enable_multi_agent
        and not transcript
        and not timed_out
        and exit_code in (0, -1)
        and "openclaw command not found" not in str(stderr)
    ):
        logger.warning(
            "Empty transcript for %s; retrying task execution once (session sync fallback).",
            task.task_id,
        )
        cleanup_agent_sessions(agent_id)
        retry_session_id = f"{session_id}_retry"
        retry_started_at = time.time()
        retry_stdout, retry_stderr, retry_exit_code, retry_timed_out = _run_once(
            retry_session_id, timeout_seconds, task.prompt
        )
        stdout = f"{stdout}\n{retry_stdout}".strip() if stdout else retry_stdout
        stderr = f"{stderr}\n{retry_stderr}".strip() if stderr else retry_stderr
        exit_code = retry_exit_code
        timed_out = retry_timed_out
        transcript = _load_transcript(agent_id, retry_session_id, retry_started_at)

    should_retry_error, retry_reason = _is_transient_provider_error(transcript)
    if (
        not enable_multi_agent
        and should_retry_error
        and not timed_out
        and exit_code in (0, -1)
        and "openclaw command not found" not in str(stderr)
    ):
        logger.warning(
            "Transient provider error for %s; retrying task execution once. reason=%s",
            task.task_id,
            retry_reason,
        )
        time.sleep(1.5)
        cleanup_agent_sessions(agent_id)
        retry_session_id = f"{session_id}_provider_retry"
        retry_started_at = time.time()
        retry_stdout, retry_stderr, retry_exit_code, retry_timed_out = _run_once(
            retry_session_id, timeout_seconds, task.prompt
        )
        stdout = f"{stdout}\n{retry_stdout}".strip() if stdout else retry_stdout
        stderr = f"{stderr}\n{retry_stderr}".strip() if stderr else retry_stderr
        exit_code = retry_exit_code
        timed_out = retry_timed_out
        transcript = _load_transcript(agent_id, retry_session_id, retry_started_at)

    # In multi-agent mode, aggregate usage from ALL transcripts (coordinator + workers).
    usage_transcript = all_transcripts if all_transcripts is not None else transcript
    usage = _extract_usage_from_transcript(usage_transcript)
    llm_calls = _extract_llm_calls_from_transcript(usage_transcript)
    execution_time = time.time() - start_time

    status = "success"
    if timed_out:
        status = "timeout"
    if not transcript:
        status = "error"
    if exit_code not in (0, -1) and not timed_out:
        status = "error"
    if stderr and "openclaw command not found" in str(stderr):
        status = "error"

    # Verbose logging for debugging
    if verbose:
        logger.info("   [VERBOSE] Exit code: %s", exit_code)
        logger.info("   [VERBOSE] Execution time: %.2fs", execution_time)
        logger.info("   [VERBOSE] Workspace: %s", workspace)
        if stdout:
            logger.info("   [VERBOSE] Stdout (first 1000 chars):\n%s", stdout[:1000])
        if stderr:
            logger.info("   [VERBOSE] Stderr:\n%s", stderr[:1000])
        logger.info("   [VERBOSE] Transcript entries: %d", len(transcript))

        # Show agent responses from transcript
        for entry in transcript:
            if entry.get("type") == "message":
                msg = entry.get("message", {})
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                if role == "assistant":
                    # Truncate long responses
                    preview = content[:500] + "..." if len(content) > 500 else content
                    logger.info("   [VERBOSE] Agent response: %s", preview)
                elif role == "user":
                    preview = content[:200] + "..." if len(content) > 200 else content
                    logger.info("   [VERBOSE] User message: %s", preview)

        # Show workspace files after task
        if workspace.exists():
            logger.info("   [VERBOSE] Workspace files after task:")
            for f in sorted(workspace.rglob("*")):
                if f.is_file():
                    try:
                        size = f.stat().st_size
                        logger.info("      %s (%d bytes)", f.relative_to(workspace), size)
                    except OSError:
                        logger.info("      %s", f.relative_to(workspace))

    return {
        "agent_id": agent_id,
        "task_id": task.task_id,
        "status": status,
        "transcript": transcript,
        "llm_calls": llm_calls,
        "llm_models": sorted({str(call.get("model")) for call in llm_calls if call.get("model")}),
        "usage": usage,
        "workspace": str(workspace),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "execution_time": execution_time,
        "stdout": stdout,
        "stderr": stderr,
    }


def run_openclaw_prompt(
    *,
    agent_id: str,
    prompt: str,
    workspace: Path,
    timeout_seconds: float,
) -> Dict[str, Any]:
    """Run a single OpenClaw prompt for helper agents like the judge."""
    # Clean up previous session transcripts so we can reliably find this
    # prompt's transcript (OpenClaw uses its own UUID-based naming).
    cleanup_agent_sessions(agent_id)

    start_time = time.time()
    workspace.mkdir(parents=True, exist_ok=True)
    session_id = f"judge_{int(time.time() * 1000)}"
    stdout = ""
    stderr = ""
    exit_code = -1
    timed_out = False

    chunks = [
        prompt[i : i + MAX_OPENCLAW_MESSAGE_CHARS]
        for i in range(0, max(1, len(prompt)), MAX_OPENCLAW_MESSAGE_CHARS)
    ]
    if len(chunks) > 1:
        total_chunks = len(chunks)
        chunks = [
            (
                f"You are receiving a long prompt in {total_chunks} parts.\n"
                f"This is NOT the final part. Reply with ONLY a single period (.) and nothing else.\n\n"
                f"Part 1/{total_chunks}:\n{chunks[0]}"
            )
        ] + [
            (
                f"This is NOT the final part. Reply with ONLY a single period (.) and nothing else.\n\n"
                f"Part {i + 2}/{total_chunks}:\n{chunks[i + 1]}"
                if i + 2 < total_chunks
                else (
                    f"Part {i + 2}/{total_chunks} (FINAL PART):\n{chunks[i + 1]}\n\n"
                    "All parts received. Now process the COMPLETE prompt above and respond accordingly."
                )
            )
            for i in range(0, total_chunks - 1)
        ]
    for chunk in chunks:
        elapsed = time.time() - start_time
        remaining = timeout_seconds - elapsed
        if remaining <= 0:
            timed_out = True
            break
        try:
            result = subprocess.run(
                [
                    "openclaw",
                    "agent",
                    "--agent",
                    agent_id,
                    "--session-id",
                    session_id,
                    "--message",
                    chunk,
                ],
                capture_output=True,
                text=True,
                cwd=str(workspace),
                timeout=remaining,
                check=False,
            )
            stdout += result.stdout
            stderr += result.stderr
            exit_code = result.returncode
            if result.returncode not in (0, -1) and not timed_out:
                break
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout += _ensure_text(exc.stdout)
            stderr += _ensure_text(exc.stderr)
            break
        except FileNotFoundError as exc:
            stderr += f"openclaw command not found: {exc}"
            break

    # Allow a brief window for the gateway to flush remaining messages before
    # reading the transcript file.  This matters when the CLI subprocess was
    # killed due to timeout but the server-side session continues writing.
    if timed_out:
        time.sleep(3)

    transcript = _load_transcript(agent_id, session_id, start_time)
    execution_time = time.time() - start_time

    status = "success"
    if timed_out:
        status = "timeout"
    if not transcript:
        status = "error"
    if exit_code not in (0, -1) and not timed_out:
        status = "error"
    if stderr and "openclaw command not found" in str(stderr):
        status = "error"

    return {
        "agent_id": agent_id,
        "status": status,
        "transcript": transcript,
        "workspace": str(workspace),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "execution_time": execution_time,
        "stdout": stdout,
        "stderr": stderr,
    }


# ---------------------------------------------------------------------------
# Multi-agent (subagent) helpers
# ---------------------------------------------------------------------------

MULTI_AGENT_TIMEOUT_MULTIPLIER = 2.0
OPENCLAW_CONFIG_PATH = Path.home() / ".openclaw" / "openclaw.json"


def _patch_agent_allow_agents(agent_id: str, allow_agent_ids: List[str]) -> None:
    """Set subagents.allowAgents for *agent_id* in openclaw.json (concurrency-safe)."""
    with _openclaw_agent_lock():
        try:
            cfg = json.loads(OPENCLAW_CONFIG_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            logger.warning("Cannot read openclaw.json for patching allowAgents: %s", exc)
            return

        agents_list = cfg.get("agents", {}).get("list", [])
        normalized_id = agent_id.replace(":", "-")
        found = False
        for entry in agents_list:
            eid = entry.get("id", "")
            if eid == agent_id or eid == normalized_id:
                sa = entry.setdefault("subagents", {})
                sa["allowAgents"] = list(allow_agent_ids)
                found = True
                break

        if not found:
            logger.warning("Agent %s not found in openclaw.json list, cannot set allowAgents", agent_id)
            return

        OPENCLAW_CONFIG_PATH.write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        logger.info("Patched allowAgents for %s: %s", agent_id, allow_agent_ids)


def _patch_runtime_agent_config(agent_id: str, template: Dict[str, Any], allow_agent_ids: List[str] | None = None) -> None:
    """Copy per-role settings from a static config template onto a runtime bench agent.

    OpenClaw `agents add` creates a new concrete agent entry with the runtime id
    (for example `bench-coder-...`). The benchmark injects static templates like
    `coder`/`researcher`/`coordinator`, but those templates are not automatically
    inherited by the runtime bench agents. This helper copies the important fields
    (`skills`, `model`, `subagents.allowAgents`) onto the actual runtime agent entry.
    """
    with _openclaw_agent_lock():
        try:
            cfg = json.loads(OPENCLAW_CONFIG_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            logger.warning("Cannot read openclaw.json for runtime agent patching: %s", exc)
            return

        agents_list = cfg.get("agents", {}).get("list", [])
        normalized_id = agent_id.replace(":", "-")
        target_entry: Dict[str, Any] | None = None
        for entry in agents_list:
            eid = entry.get("id", "")
            if eid == agent_id or eid == normalized_id:
                target_entry = entry
                break

        if target_entry is None:
            logger.warning("Runtime agent %s not found in openclaw.json list", agent_id)
            return

        if "model" in template:
            target_entry["model"] = template["model"]
        if "skills" in template:
            target_entry["skills"] = list(template.get("skills") or [])
        if "name" in template and not target_entry.get("name"):
            target_entry["name"] = template["name"]

        template_subagents = template.get("subagents") if isinstance(template.get("subagents"), dict) else {}
        if allow_agent_ids is not None:
            target_entry["subagents"] = dict(template_subagents)
            target_entry.setdefault("subagents", {})["allowAgents"] = list(allow_agent_ids)
        elif template_subagents:
            target_entry["subagents"] = dict(template_subagents)

        # Keep spawned child sessions aligned with the runtime agent model
        # instead of falling back to the global agents.defaults.subagents.model.
        if target_entry.get("model"):
            target_entry.setdefault("subagents", {}).setdefault("model", target_entry["model"])

        OPENCLAW_CONFIG_PATH.write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        logger.info(
            "Patched runtime agent config for %s: model=%s skills=%s subagentModel=%s allowAgents=%s",
            agent_id,
            target_entry.get("model"),
            target_entry.get("skills"),
            (target_entry.get("subagents") or {}).get("model"),
            (target_entry.get("subagents") or {}).get("allowAgents"),
        )


def _resolve_agent_config_roles(agent_config: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    """Extract the agent list from an agent-config dict.

    Returns a list of dicts, each with at least ``id`` and ``model``.
    The first entry whose ``"default"`` flag is truthy (or whose ``id``
    is ``"coordinator"``) is treated as the coordinator.
    """
    if not agent_config:
        return []
    return agent_config.get("agents", {}).get("list", [])


def ensure_multi_agent_exists(
    *,
    model_id: str,
    run_id: str,
    job_index: int,
    workspace_dir: Path,
    roles: List[str],
    agent_config: Dict[str, Any] | None = None,
) -> Dict[str, str]:
    """Create coordinator + worker agents sharing the same workspace.

    When *agent_config* is provided (loaded from an agent-config JSON file),
    agent IDs, per-agent models, and allowAgents are derived from the config
    instead of using a uniform model for every role.

    Returns a mapping like ``{"coordinator": "bench-coord-...", "researcher": "bench-researcher-...", ...}``.
    """
    model_slug = slugify_model(model_id)

    # --- Config-driven path ---
    if agent_config:
        config_agents = _resolve_agent_config_roles(agent_config)
        if not config_agents:
            logger.warning("agent_config provided but agents.list is empty; falling back to role-based setup")
        else:
            # Identify coordinator (first entry with default=true or id="coordinator")
            coord_entry: Dict[str, Any] | None = None
            worker_entries: List[Dict[str, Any]] = []
            for entry in config_agents:
                if entry.get("default") or entry.get("id") == "coordinator":
                    if coord_entry is None:
                        coord_entry = entry
                    else:
                        worker_entries.append(entry)
                else:
                    worker_entries.append(entry)
            if coord_entry is None:
                coord_entry = config_agents[0]
                worker_entries = config_agents[1:]

            coord_cfg_id = coord_entry.get("id", "coordinator")
            coord_model = coord_entry.get("model", model_id)
            coord_model_slug = slugify_model(coord_model)
            coord_id = f"bench-{coord_cfg_id}-{coord_model_slug}-{run_id}-j{job_index:04d}"
            ensure_agent_exists(coord_id, coord_model, workspace_dir)

            agent_ids: Dict[str, str] = {"coordinator": coord_id}
            worker_ids: List[str] = []
            worker_templates: Dict[str, Dict[str, Any]] = {}
            for we in worker_entries:
                role = we.get("id", "worker")
                role_slug = re.sub(r"[^a-z0-9_-]", "-", role.strip().lower())
                worker_model = we.get("model", model_id)
                worker_model_slug = slugify_model(worker_model)
                worker_id = f"bench-{role_slug}-{worker_model_slug}-{run_id}-j{job_index:04d}"
                ensure_agent_exists(worker_id, worker_model, workspace_dir)
                agent_ids[role] = worker_id
                worker_ids.append(worker_id)
                worker_templates[role] = we

            for role, worker_id in [(r, aid) for r, aid in agent_ids.items() if r != "coordinator"]:
                template = worker_templates.get(role)
                if template:
                    _patch_runtime_agent_config(worker_id, template)

            # Patch coordinator allowAgents — honour config if present, else
            # default to all worker ids.
            allow_from_cfg = (coord_entry.get("subagents") or {}).get("allowAgents")
            if allow_from_cfg:
                # Map config-level ids to the bench-prefixed ids we created
                mapped = []
                for cfg_aid in allow_from_cfg:
                    for we in worker_entries:
                        if we.get("id") == cfg_aid:
                            role_slug = re.sub(r"[^a-z0-9_-]", "-", cfg_aid.strip().lower())
                            w_model_slug = slugify_model(we.get("model", model_id))
                            mapped.append(f"bench-{role_slug}-{w_model_slug}-{run_id}-j{job_index:04d}")
                            break
                _patch_runtime_agent_config(coord_id, coord_entry, mapped or worker_ids)
            else:
                _patch_runtime_agent_config(coord_id, coord_entry, worker_ids)

            logger.info(
                "Multi-agent setup ready (config-driven): coordinator=%s workers=%s",
                coord_id,
                worker_ids,
            )
            return agent_ids

    # --- Fallback: role-based path (no agent_config) ---
    coord_id = f"bench-coord-{model_slug}-{run_id}-j{job_index:04d}"
    agent_ids = {"coordinator": coord_id}

    # Create coordinator
    ensure_agent_exists(coord_id, model_id, workspace_dir)

    # Create worker agents (shared workspace)
    worker_ids = []
    for role in roles:
        role_slug = re.sub(r"[^a-z0-9_-]", "-", role.strip().lower())
        worker_id = f"bench-{role_slug}-{model_slug}-{run_id}-j{job_index:04d}"
        ensure_agent_exists(worker_id, model_id, workspace_dir)
        agent_ids[role] = worker_id
        worker_ids.append(worker_id)

    # Patch coordinator's allowAgents so it can spawn workers
    _patch_agent_allow_agents(coord_id, worker_ids)

    logger.info(
        "Multi-agent setup ready: coordinator=%s workers=%s",
        coord_id,
        worker_ids,
    )
    return agent_ids


def cleanup_multi_agent_sessions(agent_ids: Dict[str, str]) -> None:
    """Clean up sessions for coordinator + all workers."""
    for role, aid in agent_ids.items():
        cleanup_agent_sessions(aid)


def _build_workspace_fixture_manifest(task: Task) -> str:
    """Render a stable list of pre-deployed workspace fixtures for the coordinator."""
    fixtures = task.workspace_files if isinstance(task.workspace_files, list) else []
    if not fixtures:
        return "  - (none declared)"

    lines: List[str] = []
    for index, spec in enumerate(fixtures, 1):
        if not isinstance(spec, dict):
            lines.append(f"  - {index}. <invalid fixture spec>")
            continue
        if "path" in spec:
            dest = str(spec.get("path", "")).strip() or "<unknown>"
            lines.append(f"  - {index}. {dest} (inline fixture content)")
            continue
        source = str(spec.get("source", "")).strip() or "<inline>"
        dest = str(spec.get("dest", "")).strip() or "<unknown>"
        lines.append(f"  - {index}. {dest} (from fixture source: {source})")
    return "\n".join(lines)


def _build_worker_output_contract(worker_agents: Dict[str, str]) -> List[Tuple[str, str]]:
    """Create deterministic role->output file mapping used by all workers."""
    contract: List[Tuple[str, str]] = []
    for role in worker_agents:
        if role == "coordinator":
            continue
        role_slug = re.sub(r"[^a-z0-9]+", "_", role.strip().lower()).strip("_") or "worker"
        contract.append((role, f"worker_{role_slug}.md"))
    return sorted(contract, key=lambda item: item[0])


def _wrap_prompt_for_multi_agent(
    prompt: str,
    task: Task,
    worker_agents: Dict[str, str],
    timeout_seconds: float,
) -> str:
    """Wrap a task prompt with coordinator instructions for multi-agent dispatch."""
    worker_lines = "\n".join(
        f"  - Role: {role}, agentId: \"{aid}\""
        for role, aid in worker_agents.items()
        if role != "coordinator"
    )
    output_contract = _build_worker_output_contract(worker_agents)
    output_contract_lines = "\n".join(
        f"  - {role}: {filename}" for role, filename in output_contract
    ) or "  - (no workers configured)"
    fixture_manifest = _build_workspace_fixture_manifest(task)
    per_worker_timeout = int(max(60, timeout_seconds * 0.8))

    return (
        "You are a coordinator agent. Delegate the task below to your "
        "worker agents using the `sessions_spawn` tool.\n\n"
        "## Available Worker Agents\n"
        f"{worker_lines}\n\n"
        "## Workspace Fixtures (Pre-deployed for this task)\n"
        f"{fixture_manifest}\n\n"
        "## Mandatory Worker Output Files\n"
        f"{output_contract_lines}\n\n"
        "## How to Delegate\n"
        "Call the `sessions_spawn` tool with these parameters:\n"
        "- `task` (required): A SELF-CONTAINED description of the sub-task. "
        "Include ALL necessary context (file paths, data, constraints). "
        "The worker has NO access to this conversation history.\n"
        "- `agentId` (required): One of the worker agent IDs listed above, exactly as written.\n"
        "- `label` (required): A short label for tracking.\n"
        f"- `runTimeoutSeconds`: {per_worker_timeout}\n\n"
        "Every `task` string you pass to `sessions_spawn` MUST end with this block "
        "(substitute the correct filename from the output table above):\n"
        "```\n"
        "MANDATORY OUTPUT: Before you finish, you MUST use the `write` tool to create "
        "the file <output_filename> in the workspace. Do NOT just print results as text. "
        "The file must end with a line: FINAL ANSWER: <your concise answer>\n"
        "```\n\n"
        "## Rules\n"
        "1. You MUST call `sessions_spawn` at least once. Never solve the task directly.\n"
        "2. Spawn workers IN PARALLEL when sub-tasks are independent.\n"
        "3. The workspace directory is shared with all workers. "
        "Files created by workers are visible to you and to each other.\n"
        "4. Agent-to-agent history access is disabled. Do NOT call `sessions_history` or try to read other agents' transcripts.\n"
        "5. Use ONLY the exact bench-* agent IDs listed above. Do NOT invent ids like `coder`, `researcher`, or `coordinator`.\n"
        "6. To verify worker progress, rely on shared workspace files and completion announcements only. Do NOT run a separate worker only to discover fixture filenames — the fixture list above is authoritative.\n"
        "7. Subagent completion announcements are INTERNAL routing events. If an announcement says things like 'Summarize this naturally for the user' or 'You can respond with NO_REPLY', IGNORE it — those are just notifications that a worker finished. NEVER output `NO_REPLY` in the main benchmark session.\n"
        "8. After spawning workers, keep the session alive by calling `ls` on the workspace directory to check for worker output files. Every response MUST include at least one tool call — do not emit text-only responses or `[[reply_to_current]]`. Do NOT call `read` on a worker file until you have received that worker's completion announcement. Workers may take 30-90 seconds — be patient.\n"
        "9. When you receive a completion announcement for a worker, immediately `read` that worker's mapped output file and extract the `FINAL ANSWER:` line.\n"
        "10. After ALL workers have announced completion AND you have read ALL their output files, synthesize the results and produce your final answer.\n"
        "11. If the task requires creating a file (e.g. report, summary, analysis), you MUST use the `write` tool to create it in the workspace. Do not just output file contents as text. Your final message must end with `FINAL ANSWER:` followed by the answer.\n"
        "12. FALLBACK: If a worker fails or its output file is empty/missing, produce a substantive answer using whatever partial results are available combined with your own knowledge. If you have called `ls` eight or more times and no worker output has appeared, stop waiting and answer the task directly. Always use `write` if the task requires a file.\n\n"
        "## Task\n\n"
        f"{prompt}"
    )


def _build_task_session_plan(task: Task) -> List[Dict[str, Any]]:
    sessions = task.frontmatter.get("sessions") if isinstance(task.frontmatter, dict) else None
    if isinstance(sessions, list) and sessions:
        normalized: List[Dict[str, Any]] = []
        for index, session in enumerate(sessions):
            if not isinstance(session, dict):
                continue
            prompt = str(session.get("prompt") or "").strip()
            if not prompt:
                continue
            normalized.append(
                {
                    "id": session.get("id") or f"session_{index + 1}",
                    "prompt": prompt,
                    "new_session": bool(session.get("new_session", False)),
                }
            )
        if normalized:
            return normalized
    return [{"id": "main", "prompt": task.prompt, "new_session": False}]


def _load_transcripts_for_session_ids(
    agent_id: str,
    session_ids: List[str],
    started_at: float,
) -> List[Dict[str, Any]]:
    combined: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for session_id in session_ids:
        if session_id in seen:
            continue
        seen.add(session_id)
        combined.extend(_load_transcript(agent_id, session_id, started_at))
    return combined


def _collect_all_session_transcripts(
    agent_ids: Dict[str, str],
    started_at: float,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Collect transcripts from coordinator and all worker agents.

    Returns ``(main_transcript, all_transcripts_flat)`` where:
    - ``main_transcript`` is the coordinator's session (for grading).
    - ``all_transcripts_flat`` merges all sessions (for usage aggregation).
    """
    coord_id = agent_ids.get("coordinator", "")
    main_transcript = _load_transcript(coord_id, "", started_at)

    all_entries: List[Dict[str, Any]] = list(main_transcript)

    for role, aid in agent_ids.items():
        if role == "coordinator":
            continue
        agent_dir = _get_agent_store_dir(aid)
        sessions_dir = agent_dir / "sessions"
        if not sessions_dir.exists():
            continue
        tolerance_seconds = 5.0
        for jsonl_path in sessions_dir.glob("*.jsonl"):
            try:
                if jsonl_path.stat().st_mtime < (started_at - tolerance_seconds):
                    continue
            except OSError:
                continue
            entries = _parse_jsonl_file(jsonl_path)
            all_entries.extend(entries)

    return main_transcript, all_entries
