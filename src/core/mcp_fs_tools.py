"""
Tool definitions (function calling) for MCP filesystem operations
and HTTP-side execution wrappers.
"""

from __future__ import annotations

import json
from typing import Any

import requests

from .mcp_http import (
    mcp_fs_delete,
    mcp_fs_list,
    mcp_fs_mkdir,
    mcp_fs_read,
    mcp_fs_rename,
    mcp_fs_stat,
    mcp_fs_write,
)
from .types import AgentConfig

# Format OpenAI / OpenRouter (chat.completions tools)
FILESYSTEM_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "fs_list_directory",
            "description": (
                "Lists directory contents on the MCP server (file/subdirectory names with is_dir flag). "
                "Use this first when the user asks what is inside a folder, searches for a file location, "
                "or before proposing a path so names are not guessed. It does not read file content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path, e.g. /home/jump or a subdirectory (relative if server cwd is inside an allowed root).",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fs_stat_path",
            "description": (
                "Checks path metadata: existence, file vs directory, size, mtime. "
                "Use before reading large files, when debugging missing paths, "
                "or when you need to confirm entry type without listing a full folder."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative path to inspect."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fs_read_file",
            "description": (
                "Reads text file content from the server (UTF-8, invalid bytes replaced). "
                "Also returns truncated/read_bytes when file size exceeds the limit. "
                "Use for logs, config files, source code, and README when the user asks to view content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read."},
                    "max_bytes": {
                        "type": "integer",
                        "description": "Optional byte limit (server default uses a safe maximum).",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fs_write_file",
            "description": (
                "Writes or overwrites a text file on the server. "
                "Use when the user asks to create/edit files, save scripts, or update configuration. "
                "Set create_parents=true if parent directories may not exist yet. "
                "Do not use for very large binary payloads."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Target file path."},
                    "content": {"type": "string", "description": "Full replacement content for the file."},
                    "encoding": {
                        "type": "string",
                        "description": "Write encoding, default utf-8.",
                        "default": "utf-8",
                    },
                    "create_parents": {
                        "type": "boolean",
                        "description": "If true, create missing parent directories before writing.",
                        "default": False,
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fs_mkdir",
            "description": (
                "Creates a directory (single level or recursive with parents=true like mkdir -p). "
                "Use before writes when a target folder does not exist."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "New directory path."},
                    "parents": {
                        "type": "boolean",
                        "description": "True = create all missing path segments.",
                        "default": False,
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fs_delete_path",
            "description": (
                "Deletes a single file or an empty directory on the server. "
                "Does not remove non-empty directories; delete inner files first. "
                "Use only when the user explicitly requests deletion."
            ),
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "File or empty directory to delete."}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fs_rename_path",
            "description": (
                "Renames or moves a file/directory inside allowed paths. "
                "Destination (to_path) must not already exist; destination parent directory must exist."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "from_path": {"type": "string", "description": "Existing source path."},
                    "to_path": {"type": "string", "description": "New destination path (file or directory)."},
                },
                "required": ["from_path", "to_path"],
            },
        },
    },
]


def _http_error_payload(exc: requests.HTTPError) -> dict[str, Any]:
    body = ""
    if exc.response is not None:
        try:
            body = exc.response.text[:4000]
        except OSError:
            body = ""
    return {
        "ok": False,
        "http_status": exc.response.status_code if exc.response is not None else None,
        "error": str(exc),
        "body": body,
    }


def run_filesystem_tool(name: str, arguments: dict[str, Any], config: AgentConfig) -> str:
    """Runs a single MCP tool call and returns a JSON string for role=tool content."""
    try:
        if name == "fs_list_directory":
            out = mcp_fs_list(config, str(arguments.get("path", ".")))
        elif name == "fs_stat_path":
            out = mcp_fs_stat(config, str(arguments["path"]))
        elif name == "fs_read_file":
            path = str(arguments["path"])
            mb = arguments.get("max_bytes")
            out = mcp_fs_read(config, path, int(mb) if mb is not None else None)
        elif name == "fs_write_file":
            out = mcp_fs_write(
                config,
                path=str(arguments["path"]),
                content=str(arguments.get("content", "")),
                encoding=str(arguments.get("encoding") or "utf-8"),
                create_parents=bool(arguments.get("create_parents", False)),
            )
        elif name == "fs_mkdir":
            out = mcp_fs_mkdir(
                config,
                path=str(arguments["path"]),
                parents=bool(arguments.get("parents", False)),
            )
        elif name == "fs_delete_path":
            out = mcp_fs_delete(config, str(arguments["path"]))
        elif name == "fs_rename_path":
            out = mcp_fs_rename(config, str(arguments["from_path"]), str(arguments["to_path"]))
        else:
            return json.dumps({"ok": False, "error": f"Unknown tool: {name}"}, ensure_ascii=False)
        return json.dumps(out, ensure_ascii=False)
    except requests.HTTPError as exc:
        return json.dumps(_http_error_payload(exc), ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
