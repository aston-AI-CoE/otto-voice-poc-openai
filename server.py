"""
Otto Voice POC — OpenAI Realtime API

Voice interface for Otto using real BashExecutor + memory tools from Otto's codebase.
WebRTC for audio (browser ↔ OpenAI), server executes tools.

Routes:
  GET  /             → client.html
  POST /session      → create OpenAI Realtime session, return ephemeral key
  POST /session/{id}/tool → execute a tool call
  DELETE /session/{id}    → cleanup executor
"""

import json
import logging
import os
import sys
import types
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

# ---------------------------------------------------------------------------
# Path setup — Otto libs live outside this project
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).parent
sys.path.insert(0, str(_ROOT / "stubs"))          # claude_agent_sdk stub
sys.path.insert(0, "/root/otto/library/bash")
sys.path.insert(0, "/root/otto/services/worker")

# Pre-inject stub for otto.worker.memory package so its __init__.py
# (which pulls in otto.contexts → otto.orm chain) doesn't run.
# We only need tools.py, which loads fine once claude_agent_sdk is stubbed.
_mem_pkg = types.ModuleType("otto.worker.memory")
_mem_pkg.__path__ = ["/root/otto/services/worker/otto/worker/memory"]
_mem_pkg.__package__ = "otto.worker.memory"
sys.modules.setdefault("otto.worker.memory", _mem_pkg)

from otto.bash import BashExecutor  # noqa: E402
from otto.worker.memory.tools import (  # noqa: E402
    delete_memory,
    list_memories,
    recall_memories,
    store_memory,
)

load_dotenv()

# DB env vars (needed by otto.worker.memory.db at runtime)
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_NAME", "otto")
os.environ.setdefault("DB_USER", "otto")
os.environ.setdefault("DB_PASSWORD", "otto")
os.environ.setdefault("REDIS_HOST", "127.0.0.1")
os.environ.setdefault("CLAUDE_CONFIG_DIR", "/tmp")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-20s  %(levelname)-5s  %(message)s",
)
logger = logging.getLogger("otto-voice-poc")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_REALTIME_MODEL", "gpt-realtime")
OPENAI_VOICE = os.environ.get("OPENAI_VOICE", "ash")

AGENT_ID = "agent_7edf103e-f8d8-4dc5-9291-89e2ceb78cf1"

ROOT = Path(__file__).parent
PROMPT = (ROOT / "prompt.txt").read_text()


# ---------------------------------------------------------------------------
# FakeRun — minimal context object required by Otto memory tools
# ---------------------------------------------------------------------------
class FakeRun:
    agent_id = AGENT_ID
    session_id: str

    def __init__(self, session_id: str):
        self.session_id = session_id

    class session:
        session_id = "voice-poc"


# ---------------------------------------------------------------------------
# Tool definitions passed to OpenAI at session creation
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "name": "Bash",
        "description": (
            "Execute a bash command in a persistent shell session. "
            "The session preserves working directory, environment variables, and state across commands. "
            "If the command finishes within the timeout, returns output and exit_code. "
            "If it's still running, returns a pid — use BashOutput to read more output, KillBash to stop it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute.",
                },
                "timeout": {
                    "type": "number",
                    "description": "Timeout in milliseconds (default 30000, max 120000).",
                },
            },
            "required": ["command"],
        },
    },
    {
        "type": "function",
        "name": "BashOutput",
        "description": (
            "Read buffered output from a long-running command started with Bash. "
            "Output is cleared after each read. exit_code is null if still running."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pid": {
                    "type": "integer",
                    "description": "The pid returned by Bash.",
                },
            },
            "required": ["pid"],
        },
    },
    {
        "type": "function",
        "name": "KillBash",
        "description": "Terminate a long-running background process started with Bash.",
        "parameters": {
            "type": "object",
            "properties": {
                "pid": {
                    "type": "integer",
                    "description": "The pid of the process to terminate.",
                },
            },
            "required": ["pid"],
        },
    },
    {
        "type": "function",
        "name": "store_memory",
        "description": "Save information for future reference. Use when you learn something worth remembering — user preferences, important facts, decisions, or skills. Duplicate content is automatically ignored.",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The information to remember"},
                "memory_type": {
                    "type": "string",
                    "description": "What kind of information this is",
                    "enum": ["session", "preference", "skill"],
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Labels to help find this memory later",
                },
                "importance": {
                    "type": "number",
                    "description": "How important this is to remember (0.0 = low, 1.0 = critical, default 0.5)",
                },
                "source_description": {
                    "type": "string",
                    "description": "Where you learned this, e.g. 'user told me'",
                },
            },
            "required": ["content", "memory_type"],
        },
    },
    {
        "type": "function",
        "name": "recall_memories",
        "description": "Search for previously stored memories. Use when you need to remember something about the user, a past decision, or any previously stored information.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What you're trying to remember",
                },
                "memory_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Only search these memory types, e.g. ['preference', 'skill']",
                },
                "top_k": {
                    "type": "integer",
                    "description": "How many results to return (default 10)",
                },
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "list_memories",
        "description": "Browse all stored memories with optional filters. Use when you need an overview of what you remember.",
        "parameters": {
            "type": "object",
            "properties": {
                "memory_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Only return memories of these types",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Only return memories that have ALL of these tags",
                },
                "limit": {
                    "type": "integer",
                    "description": "How many memories to return (default 20, max 200)",
                },
                "sort_by": {
                    "type": "string",
                    "description": "How to order results",
                    "enum": ["created_at", "accessed_at", "importance"],
                },
            },
        },
    },
    {
        "type": "function",
        "name": "delete_memory",
        "description": "Remove a previously stored memory. Use when the user asks you to forget something or when information is no longer accurate.",
        "parameters": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "The ID of the memory to delete (obtain this from recall_memories first)",
                },
                "reason": {
                    "type": "string",
                    "description": "Why the memory is being deleted",
                },
            },
            "required": ["memory_id"],
        },
    },
]

app = FastAPI(title="Otto Voice POC – OpenAI Realtime")

# session_id → { executor: BashExecutor, run: FakeRun, created_at: str }
sessions: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
async def index():
    return FileResponse(ROOT / "client.html", media_type="text/html")


@app.post("/session")
async def create_session():
    """Create an OpenAI Realtime session with Otto's tools, return ephemeral key."""
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured")

    session_id = str(uuid.uuid4())

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.openai.com/v1/realtime/client_secrets",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "session": {
                    "type": "realtime",
                    "model": OPENAI_MODEL,
                    "instructions": PROMPT,
                    "tools": TOOLS,
                    "audio": {
                        "output": {"voice": OPENAI_VOICE},
                    },
                },
            },
            timeout=15.0,
        )

    if resp.status_code != 200:
        logger.error("OpenAI session creation failed: %s %s", resp.status_code, resp.text)
        raise HTTPException(status_code=502, detail="Failed to create OpenAI Realtime session")

    client_secret = resp.json()["value"]

    sessions[session_id] = {
        "id": session_id,
        "executor": BashExecutor(cwd="/tmp"),
        "run": FakeRun(session_id=session_id),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    logger.info("Session %s created", session_id)
    return {
        "session_id": session_id,
        "client_secret": client_secret,
        "model": OPENAI_MODEL,
    }


@app.post("/session/{session_id}/tool")
async def execute_tool(session_id: str, request: Request):
    """
    Execute a tool call on behalf of OpenAI Realtime.
    Body: { "name": "ToolName", "arguments": { ... } }
    Returns the tool result as JSON.
    """
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    body = await request.json()
    tool_name: str = body.get("name", "")
    args: dict = body.get("arguments", {})

    executor: BashExecutor = sessions[session_id]["executor"]
    run: FakeRun = sessions[session_id]["run"]
    logger.info("Tool call [%s] %s args=%s", session_id[:8], tool_name, json.dumps(args)[:120])

    try:
        if tool_name == "Bash":
            command: str = args.get("command", "")
            if not command:
                return JSONResponse({"error": "command is required"})
            timeout_ms = min(float(args.get("timeout", 30_000)), 120_000)
            timeout_s = timeout_ms / 1000
            output, exit_code = await executor.run(command, timeout=timeout_s)
            if exit_code is not None:
                result = {"output": output, "exit_code": exit_code}
            else:
                pid = await executor.start(command)
                result = {"pid": pid, "running": True, "output": output}
            logger.info("Bash result exit_code=%s output_len=%d", exit_code, len(output))
            return JSONResponse(result)

        elif tool_name == "BashOutput":
            pid: int = args.get("pid")
            if pid is None:
                return JSONResponse({"error": "pid is required"})
            output, exit_code = await executor.output(pid)
            return JSONResponse({"output": output, "exit_code": exit_code})

        elif tool_name == "KillBash":
            pid = args.get("pid")
            if pid is None:
                return JSONResponse({"error": "pid is required"})
            killed = await executor.kill(pid)
            return JSONResponse({"killed": killed, "pid": pid})

        elif tool_name == "store_memory":
            raw = await store_memory(run).handler(args)
            # raw is {"content": [{"type": "text", "text": "...json..."}]}
            return JSONResponse(json.loads(raw["content"][0]["text"]))

        elif tool_name == "recall_memories":
            raw = await recall_memories(run).handler(args)
            return JSONResponse(json.loads(raw["content"][0]["text"]))

        elif tool_name == "list_memories":
            raw = await list_memories(run).handler(args)
            return JSONResponse(json.loads(raw["content"][0]["text"]))

        elif tool_name == "delete_memory":
            raw = await delete_memory(run).handler(args)
            return JSONResponse(json.loads(raw["content"][0]["text"]))

        else:
            return JSONResponse({"error": f"Unknown tool: {tool_name}"})

    except Exception as exc:
        logger.exception("Tool execution error: %s", exc)
        return JSONResponse({"error": str(exc)})


@app.delete("/session/{session_id}")
async def end_session(session_id: str):
    """Clean up the executor when the call ends."""
    session = sessions.pop(session_id, None)
    if session:
        await session["executor"].kill_all()
        logger.info("Session %s ended, executor cleaned up", session_id)
    return {"status": "ended"}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
