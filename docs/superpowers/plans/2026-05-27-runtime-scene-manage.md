# Runtime Scene Manage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `runtime_scene_manage` so an agent can spawn/remove runtime nodes, persist the matching scene changes, write a journal entry, and create git commits.

**Architecture:** Add a Python transaction layer that validates the request, checkpoints git, calls the game runtime helper, calls a new editor persistence handler, writes a journal file, and commits the result. Keep game-only mutation in `runtime/game_helper.gd`, editor/source mutation in `handlers/runtime_scene_handler.gd`, and git/journal concerns in small Python helpers.

**Tech Stack:** Python 3.11+, FastMCP, pytest, Godot 4.x GDScript, Godot `EngineDebugger`, `EditorInterface`, `EditorUndoRedoManager`, git CLI.

---

## File Structure

- Create `src/runtime_studio/runtime_scene/__init__.py`: package marker for transaction support.
- Create `src/runtime_studio/runtime_scene/schema.py`: validates spawn/remove payloads and result-state names without needing a live Godot session.
- Create `src/runtime_studio/runtime_scene/git_ops.py`: small async/sync wrapper around `git status`, `git add -A`, and `git commit`.
- Create `src/runtime_studio/runtime_scene/journal.py`: writes JSON journal entries under `.runtime-studio/journal`.
- Create `src/runtime_studio/handlers/runtime_scene.py`: orchestrates full transactions.
- Create `src/runtime_studio/tools/runtime_scene.py`: registers the `runtime_scene_manage` MCP meta-tool.
- Modify `src/runtime_studio/server.py`: imports/registers `runtime_scene_manage` and documents it in server instructions.
- Modify `src/runtime_studio/tools/domains.py`: adds the `runtime_scene` domain to domain filtering metadata.
- Modify `plugin/addons/runtime_studio/runtime/game_helper.gd`: adds live `spawn_node` and `remove_node` game commands.
- Create `plugin/addons/runtime_studio/handlers/runtime_scene_handler.gd`: persists runtime scene mutations into the editor/source scene and saves.
- Modify `plugin/addons/runtime_studio/plugin.gd`: instantiates/registers the new editor handler commands.
- Create `tests/unit/test_runtime_scene_schema.py`: unit tests for payload validation and result states.
- Create `tests/unit/test_runtime_scene_git_journal.py`: unit tests for checkpoint/final commit and journal behavior.
- Create `tests/unit/test_runtime_scene_handlers.py`: unit tests for Python orchestration using stub runtimes.
- Create `tests/unit/test_runtime_scene_tool_registration.py`: registration/domain tests for `runtime_scene_manage`.
- Create `tests/unit/test_runtime_scene_gdscript_static.py`: source-level tests that lock key GDScript command names and refusal states.

---

### Task 1: Schema And Result States

**Files:**
- Create: `src/runtime_studio/runtime_scene/__init__.py`
- Create: `src/runtime_studio/runtime_scene/schema.py`
- Test: `tests/unit/test_runtime_scene_schema.py`

- [ ] **Step 1: Write failing schema tests**

Create `tests/unit/test_runtime_scene_schema.py`:

```python
from __future__ import annotations

import pytest

from runtime_studio.runtime_scene.schema import (
    RESULT_INTENT_REQUIRED,
    RESULT_PERSISTED,
    RESULT_PREFLIGHT_FAILED,
    RESULT_UNSUPPORTED_PERSISTENCE,
    RuntimeSceneValidationError,
    normalize_remove_request,
    normalize_spawn_request,
)


def test_spawn_rejects_scene_path_and_node_type_together():
    with pytest.raises(RuntimeSceneValidationError, match="Provide exactly one"):
        normalize_spawn_request(
            {
                "parent_path": "/Main",
                "name": "Bad",
                "scene_path": "res://thing.tscn",
                "node_type": "Node3D",
            }
        )


def test_spawn_rejects_missing_scene_path_and_node_type():
    with pytest.raises(RuntimeSceneValidationError, match="Provide exactly one"):
        normalize_spawn_request({"parent_path": "/Main", "name": "Bad"})


def test_spawn_rejects_non_res_scene_path():
    with pytest.raises(RuntimeSceneValidationError, match="scene_path must start with res://"):
        normalize_spawn_request(
            {"parent_path": "/Main", "scene_path": "C:/tmp/thing.tscn"}
        )


def test_spawn_accepts_raw_node_tree_with_nested_resources():
    request = normalize_spawn_request(
        {
            "parent_path": "/Main/Props",
            "name": "Crate",
            "node_type": "StaticBody3D",
            "children": [
                {
                    "name": "Mesh",
                    "node_type": "MeshInstance3D",
                    "properties": {
                        "mesh": {
                            "__class__": "BoxMesh",
                            "size": {"x": 1, "y": 2, "z": 3},
                        }
                    },
                }
            ],
        }
    )

    assert request["mode"] == "node"
    assert request["node_type"] == "StaticBody3D"
    assert request["children"][0]["properties"]["mesh"]["__class__"] == "BoxMesh"


def test_remove_requires_path():
    with pytest.raises(RuntimeSceneValidationError, match="path is required"):
        normalize_remove_request({})


def test_result_state_constants_are_stable():
    assert RESULT_PERSISTED == "persisted"
    assert RESULT_INTENT_REQUIRED == "intent_required"
    assert RESULT_UNSUPPORTED_PERSISTENCE == "unsupported_persistence"
    assert RESULT_PREFLIGHT_FAILED == "preflight_failed"
```

- [ ] **Step 2: Run schema tests and verify they fail**

Run:

```powershell
uv run pytest tests/unit/test_runtime_scene_schema.py -q
```

Expected: fail with `ModuleNotFoundError: No module named 'runtime_studio.runtime_scene'`.

- [ ] **Step 3: Add schema implementation**

Create `src/runtime_studio/runtime_scene/__init__.py`:

```python
"""Runtime scene mutation transaction support."""
```

Create `src/runtime_studio/runtime_scene/schema.py`:

```python
"""Validation helpers for runtime scene transactions."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

RESULT_PERSISTED = "persisted"
RESULT_INTENT_REQUIRED = "intent_required"
RESULT_UNSUPPORTED_PERSISTENCE = "unsupported_persistence"
RESULT_PREFLIGHT_FAILED = "preflight_failed"
RESULT_PARTIAL_FAILURE = "partial_failure"


class RuntimeSceneValidationError(ValueError):
    """Raised when a runtime scene request is malformed before side effects."""


def _require_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key, "")
    if not isinstance(value, str) or not value.strip():
        raise RuntimeSceneValidationError(f"{key} is required")
    return value.strip()


def normalize_spawn_request(payload: dict[str, Any]) -> dict[str, Any]:
    request = deepcopy(payload)
    scene_path = request.get("scene_path", "")
    node_type = request.get("node_type", "")
    has_scene_path = isinstance(scene_path, str) and bool(scene_path.strip())
    has_node_type = isinstance(node_type, str) and bool(node_type.strip())
    if has_scene_path == has_node_type:
        raise RuntimeSceneValidationError("Provide exactly one of scene_path or node_type")
    parent_path = _require_string(request, "parent_path")
    request["parent_path"] = parent_path
    if has_scene_path:
        scene_path = scene_path.strip()
        if not scene_path.startswith("res://"):
            raise RuntimeSceneValidationError("scene_path must start with res://")
        request["scene_path"] = scene_path
        request["mode"] = "scene_instance"
        request.pop("node_type", None)
    else:
        request["node_type"] = node_type.strip()
        request["mode"] = "node"
        request.pop("scene_path", None)
    children = request.get("children", [])
    if children is None:
        request["children"] = []
    elif not isinstance(children, list):
        raise RuntimeSceneValidationError("children must be an array")
    properties = request.get("properties", {})
    if properties is None:
        request["properties"] = {}
    elif not isinstance(properties, dict):
        raise RuntimeSceneValidationError("properties must be an object")
    return request


def normalize_remove_request(payload: dict[str, Any]) -> dict[str, Any]:
    request = deepcopy(payload)
    request["path"] = _require_string(request, "path")
    return request
```

- [ ] **Step 4: Run schema tests and verify they pass**

Run:

```powershell
uv run pytest tests/unit/test_runtime_scene_schema.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/runtime_studio/runtime_scene/__init__.py src/runtime_studio/runtime_scene/schema.py tests/unit/test_runtime_scene_schema.py
git commit -m "Add runtime scene request schema"
```

---

### Task 2: Git Checkpoint And Journal Helpers

**Files:**
- Create: `src/runtime_studio/runtime_scene/git_ops.py`
- Create: `src/runtime_studio/runtime_scene/journal.py`
- Test: `tests/unit/test_runtime_scene_git_journal.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_runtime_scene_git_journal.py`:

```python
from __future__ import annotations

import json
import subprocess

from runtime_studio.runtime_scene.git_ops import GitOps
from runtime_studio.runtime_scene.journal import write_journal_entry


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)


def test_git_ops_checkpoint_commits_dirty_worktree(tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "runtime-studio@example.invalid")
    _git(tmp_path, "config", "user.name", "Runtime Studio Test")
    (tmp_path / "project.godot").write_text("[application]\n", encoding="utf-8")

    ops = GitOps(tmp_path)
    result = ops.checkpoint_if_dirty("Runtime Studio checkpoint: before runtime change")

    assert result.created is True
    assert result.commit_hash
    assert "Runtime Studio checkpoint" in _git(tmp_path, "log", "-1", "--pretty=%s").stdout


def test_git_ops_checkpoint_noops_when_clean(tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "runtime-studio@example.invalid")
    _git(tmp_path, "config", "user.name", "Runtime Studio Test")
    (tmp_path / "project.godot").write_text("[application]\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "Initial")

    result = GitOps(tmp_path).checkpoint_if_dirty("Runtime Studio checkpoint: before runtime change")

    assert result.created is False
    assert result.commit_hash is None


def test_write_journal_entry_uses_project_local_runtime_studio_dir(tmp_path):
    entry = write_journal_entry(
        tmp_path,
        {
            "operation_id": "op-123",
            "operation": "spawn_node",
            "state": "persisted",
        },
    )

    assert entry.path.parent == tmp_path / ".runtime-studio" / "journal"
    data = json.loads(entry.path.read_text(encoding="utf-8"))
    assert data["operation_id"] == "op-123"
    assert data["state"] == "persisted"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
uv run pytest tests/unit/test_runtime_scene_git_journal.py -q
```

Expected: fail because `git_ops.py` and `journal.py` do not exist.

- [ ] **Step 3: Add helper implementations**

Create `src/runtime_studio/runtime_scene/git_ops.py`:

```python
"""Git helpers for runtime scene transactions."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitCommitResult:
    created: bool
    commit_hash: str | None
    message: str


class GitOps:
    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root)

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.project_root,
            text=True,
            capture_output=True,
            check=True,
        )

    def is_dirty(self) -> bool:
        return bool(self._git("status", "--porcelain").stdout.strip())

    def checkpoint_if_dirty(self, message: str) -> GitCommitResult:
        if not self.is_dirty():
            return GitCommitResult(created=False, commit_hash=None, message=message)
        return self.commit_all(message)

    def commit_all(self, message: str) -> GitCommitResult:
        self._git("add", "-A")
        self._git("commit", "-m", message)
        commit_hash = self._git("rev-parse", "HEAD").stdout.strip()
        return GitCommitResult(created=True, commit_hash=commit_hash, message=message)
```

Create `src/runtime_studio/runtime_scene/journal.py`:

```python
"""Journal writing for runtime scene transactions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class JournalEntryResult:
    path: Path


def _safe_slug(value: str) -> str:
    allowed = []
    for char in value.lower():
        if char.isalnum():
            allowed.append(char)
        elif char in {"-", "_"}:
            allowed.append(char)
        else:
            allowed.append("-")
    slug = "".join(allowed).strip("-")
    return slug or "operation"


def write_journal_entry(project_root: str | Path, entry: dict[str, Any]) -> JournalEntryResult:
    root = Path(project_root)
    journal_dir = root / ".runtime-studio" / "journal"
    journal_dir.mkdir(parents=True, exist_ok=True)
    operation = str(entry.get("operation", "operation"))
    operation_id = str(entry.get("operation_id", "unknown"))
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    path = journal_dir / f"{stamp}-{_safe_slug(operation)}-{_safe_slug(operation_id)}.json"
    path.write_text(json.dumps(entry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return JournalEntryResult(path=path)
```

- [ ] **Step 4: Run helper tests**

Run:

```powershell
uv run pytest tests/unit/test_runtime_scene_git_journal.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/runtime_studio/runtime_scene/git_ops.py src/runtime_studio/runtime_scene/journal.py tests/unit/test_runtime_scene_git_journal.py
git commit -m "Add runtime scene git and journal helpers"
```

---

### Task 3: Python Transaction Orchestrator

**Files:**
- Create: `src/runtime_studio/handlers/runtime_scene.py`
- Test: `tests/unit/test_runtime_scene_handlers.py`

- [ ] **Step 1: Write failing orchestration tests**

Create `tests/unit/test_runtime_scene_handlers.py`:

```python
from __future__ import annotations

import pytest

from runtime_studio.handlers import runtime_scene


class StubRuntime:
    def __init__(self):
        self.commands = []
        self.project_root = ""

    async def send_command(self, command, params=None, timeout=5.0, session_id=None):
        self.commands.append({"command": command, "params": params or {}, "timeout": timeout})
        if command == "get_editor_state":
            return {
                "project_path": self.project_root,
                "current_scene": "res://main.tscn",
                "is_playing": True,
                "game_capture_ready": True,
            }
        if command == "runtime_scene_resolve_spawn":
            return {
                "state": "resolved",
                "scene_file": "res://main.tscn",
                "parent_editor_path": "/Main/Props",
                "candidate_scene_paths": ["res://main.tscn"],
            }
        if command == "game_command":
            return {
                "state": "spawned",
                "runtime_path": "/Main/Props/Torch",
                "operation_id": "op-runtime",
            }
        if command == "runtime_scene_persist_spawn":
            return {
                "state": "persisted",
                "path": "/Main/Props/Torch",
                "saved_files": ["res://main.tscn"],
            }
        raise AssertionError(f"unexpected command {command}")


class FakeGitOps:
    def __init__(self):
        self.checkpoints = []
        self.commits = []

    def checkpoint_if_dirty(self, message):
        self.checkpoints.append(message)
        return type("Result", (), {"created": True, "commit_hash": "abc123", "message": message})()

    def commit_all(self, message):
        self.commits.append(message)
        return type("Result", (), {"created": True, "commit_hash": "def456", "message": message})()


@pytest.mark.asyncio
async def test_spawn_transaction_checkpoints_spawns_persists_journals_and_commits(tmp_path, monkeypatch):
    runtime = StubRuntime()
    runtime.project_root = str(tmp_path)
    git_ops = FakeGitOps()
    monkeypatch.setattr(runtime_scene, "GitOps", lambda project_root: git_ops)
    journal_calls = []
    monkeypatch.setattr(
        runtime_scene,
        "write_journal_entry",
        lambda project_root, entry: journal_calls.append(entry)
        or type("Journal", (), {"path": tmp_path / ".runtime-studio" / "journal" / "entry.json"})(),
    )

    result = await runtime_scene.spawn_node(
        runtime,
        parent_path="/Main/Props",
        name="Torch",
        scene_path="res://props/torch.tscn",
    )

    assert result["state"] == "persisted"
    assert git_ops.checkpoints == ["Runtime Studio checkpoint: before runtime change"]
    assert git_ops.commits == ["Runtime Studio: spawn Torch under /Main/Props"]
    assert [call["command"] for call in runtime.commands] == [
        "get_editor_state",
        "runtime_scene_resolve_spawn",
        "game_command",
        "runtime_scene_persist_spawn",
    ]
    assert journal_calls[0]["operation"] == "spawn_node"
    assert result["preflight_commit"] == "abc123"
    assert result["final_commit"] == "def456"


@pytest.mark.asyncio
async def test_spawn_stops_before_game_when_editor_requires_intent(tmp_path, monkeypatch):
    class IntentRuntime(StubRuntime):
        async def send_command(self, command, params=None, timeout=5.0, session_id=None):
            self.commands.append({"command": command, "params": params or {}, "timeout": timeout})
            if command == "get_editor_state":
                return {"project_path": str(tmp_path), "current_scene": "res://main.tscn"}
            if command == "runtime_scene_resolve_spawn":
                return {
                    "state": "intent_required",
                    "message": "Choose whether to edit Main.tscn or Weapon.tscn.",
                    "candidate_scene_paths": ["res://main.tscn", "res://weapon.tscn"],
                }
            raise AssertionError(f"unexpected command {command}")

    monkeypatch.setattr(runtime_scene, "GitOps", lambda project_root: FakeGitOps())

    result = await runtime_scene.spawn_node(
        IntentRuntime(),
        parent_path="/Main/Player/Weapon",
        node_type="Node3D",
    )

    assert result["state"] == "intent_required"
    assert [call["command"] for call in result["debug_commands"]] == [
        "get_editor_state",
        "runtime_scene_resolve_spawn",
    ]
```

- [ ] **Step 2: Run orchestration tests and verify they fail**

Run:

```powershell
uv run pytest tests/unit/test_runtime_scene_handlers.py -q
```

Expected: fail because `runtime_studio.handlers.runtime_scene` does not exist.

- [ ] **Step 3: Add orchestrator implementation**

Create `src/runtime_studio/handlers/runtime_scene.py`:

```python
"""Transaction handlers for runtime scene mutation."""

from __future__ import annotations

import uuid
from typing import Any

from runtime_studio.runtime.direct import DirectRuntime
from runtime_studio.runtime_scene.git_ops import GitOps
from runtime_studio.runtime_scene.journal import write_journal_entry
from runtime_studio.runtime_scene.schema import (
    RESULT_INTENT_REQUIRED,
    RESULT_PERSISTED,
    RESULT_PREFLIGHT_FAILED,
    RESULT_UNSUPPORTED_PERSISTENCE,
    RuntimeSceneValidationError,
    normalize_remove_request,
    normalize_spawn_request,
)

RUNTIME_SCENE_TIMEOUT_SEC = 20.0


def _project_root_from_state(state: dict[str, Any]) -> str:
    project_root = str(state.get("project_path") or state.get("project_root") or "")
    if not project_root:
        raise RuntimeSceneValidationError("Editor state did not include project_path")
    return project_root


def _spawn_commit_message(request: dict[str, Any]) -> str:
    name = str(request.get("name") or request.get("scene_path") or request.get("node_type"))
    return f"Runtime Studio: spawn {name} under {request['parent_path']}"


def _remove_commit_message(request: dict[str, Any]) -> str:
    return f"Runtime Studio: remove {request['path']}"


def _response(
    *,
    state: str,
    operation_id: str,
    operation: str,
    preflight_commit: str | None = None,
    final_commit: str | None = None,
    journal_path: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "state": state,
        "operation_id": operation_id,
        "operation": operation,
        "preflight_commit": preflight_commit,
        "final_commit": final_commit,
        "journal_path": journal_path,
        **extra,
    }


async def spawn_node(
    runtime: DirectRuntime,
    parent_path: str,
    name: str = "",
    scene_path: str = "",
    node_type: str = "",
    properties: dict[str, Any] | None = None,
    children: list[dict[str, Any]] | None = None,
    transform: dict[str, Any] | None = None,
) -> dict[str, Any]:
    operation_id = str(uuid.uuid4())
    request = normalize_spawn_request(
        {
            "operation_id": operation_id,
            "parent_path": parent_path,
            "name": name,
            "scene_path": scene_path,
            "node_type": node_type,
            "properties": properties or {},
            "children": children or [],
            "transform": transform or {},
        }
    )
    state = await runtime.send_command("get_editor_state")
    project_root = _project_root_from_state(state)
    git_ops = GitOps(project_root)
    try:
        preflight = git_ops.checkpoint_if_dirty("Runtime Studio checkpoint: before runtime change")
    except Exception as exc:  # noqa: BLE001
        return _response(
            state=RESULT_PREFLIGHT_FAILED,
            operation_id=operation_id,
            operation="spawn_node",
            error=str(exc),
        )

    resolve = await runtime.send_command(
        "runtime_scene_resolve_spawn",
        request,
        timeout=RUNTIME_SCENE_TIMEOUT_SEC,
    )
    if resolve.get("state") in {RESULT_INTENT_REQUIRED, RESULT_UNSUPPORTED_PERSISTENCE}:
        return _response(
            state=str(resolve["state"]),
            operation_id=operation_id,
            operation="spawn_node",
            preflight_commit=preflight.commit_hash,
            resolution=resolve,
            debug_commands=getattr(runtime, "commands", []),
        )

    runtime_result = await runtime.send_command(
        "game_command",
        {"op": "spawn_node", "params": request},
        timeout=RUNTIME_SCENE_TIMEOUT_SEC,
    )
    persist = await runtime.send_command(
        "runtime_scene_persist_spawn",
        {"request": request, "resolution": resolve, "runtime_result": runtime_result},
        timeout=RUNTIME_SCENE_TIMEOUT_SEC,
    )
    journal = write_journal_entry(
        project_root,
        {
            "operation_id": operation_id,
            "operation": "spawn_node",
            "state": RESULT_PERSISTED,
            "request": request,
            "resolution": resolve,
            "runtime_result": runtime_result,
            "persistence_result": persist,
            "preflight_commit": preflight.commit_hash,
        },
    )
    final = git_ops.commit_all(_spawn_commit_message(request))
    return _response(
        state=RESULT_PERSISTED,
        operation_id=operation_id,
        operation="spawn_node",
        preflight_commit=preflight.commit_hash,
        final_commit=final.commit_hash,
        journal_path=str(journal.path),
        runtime_result=runtime_result,
        persistence_result=persist,
    )


async def remove_node(runtime: DirectRuntime, path: str) -> dict[str, Any]:
    operation_id = str(uuid.uuid4())
    request = normalize_remove_request({"operation_id": operation_id, "path": path})
    state = await runtime.send_command("get_editor_state")
    project_root = _project_root_from_state(state)
    git_ops = GitOps(project_root)
    try:
        preflight = git_ops.checkpoint_if_dirty("Runtime Studio checkpoint: before runtime change")
    except Exception as exc:  # noqa: BLE001
        return _response(
            state=RESULT_PREFLIGHT_FAILED,
            operation_id=operation_id,
            operation="remove_node",
            error=str(exc),
        )
    resolve = await runtime.send_command("runtime_scene_resolve_remove", request)
    if resolve.get("state") in {RESULT_INTENT_REQUIRED, RESULT_UNSUPPORTED_PERSISTENCE}:
        return _response(
            state=str(resolve["state"]),
            operation_id=operation_id,
            operation="remove_node",
            preflight_commit=preflight.commit_hash,
            resolution=resolve,
        )
    runtime_result = await runtime.send_command(
        "game_command",
        {"op": "remove_node", "params": request},
        timeout=RUNTIME_SCENE_TIMEOUT_SEC,
    )
    persist = await runtime.send_command(
        "runtime_scene_persist_remove",
        {"request": request, "resolution": resolve, "runtime_result": runtime_result},
        timeout=RUNTIME_SCENE_TIMEOUT_SEC,
    )
    journal = write_journal_entry(
        project_root,
        {
            "operation_id": operation_id,
            "operation": "remove_node",
            "state": RESULT_PERSISTED,
            "request": request,
            "resolution": resolve,
            "runtime_result": runtime_result,
            "persistence_result": persist,
            "preflight_commit": preflight.commit_hash,
        },
    )
    final = git_ops.commit_all(_remove_commit_message(request))
    return _response(
        state=RESULT_PERSISTED,
        operation_id=operation_id,
        operation="remove_node",
        preflight_commit=preflight.commit_hash,
        final_commit=final.commit_hash,
        journal_path=str(journal.path),
        runtime_result=runtime_result,
        persistence_result=persist,
    )
```

- [ ] **Step 4: Run orchestration tests**

Run:

```powershell
uv run pytest tests/unit/test_runtime_scene_handlers.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/runtime_studio/handlers/runtime_scene.py tests/unit/test_runtime_scene_handlers.py
git commit -m "Add runtime scene transaction orchestration"
```

---

### Task 4: MCP Tool Registration

**Files:**
- Create: `src/runtime_studio/tools/runtime_scene.py`
- Modify: `src/runtime_studio/server.py`
- Modify: `src/runtime_studio/tools/domains.py`
- Test: `tests/unit/test_runtime_scene_tool_registration.py`

- [ ] **Step 1: Write failing registration tests**

Create `tests/unit/test_runtime_scene_tool_registration.py`:

```python
from __future__ import annotations

import asyncio

from runtime_studio.server import create_server
from runtime_studio.tools._meta_tool import MANAGE_TOOL_OPS, MANAGE_TOOL_RESOURCE_FORMS


def _tool_names(server):
    return {tool.name for tool in asyncio.run(server.list_tools())}


def test_runtime_scene_manage_is_registered():
    server = create_server(ws_port=0)

    assert "runtime_scene_manage" in _tool_names(server)
    assert MANAGE_TOOL_OPS["runtime_scene_manage"] == (
        "spawn_node",
        "remove_node",
        "journal_list",
        "journal_get",
    )


def test_runtime_scene_manage_declares_read_resource_waivers():
    create_server(ws_port=0)

    assert MANAGE_TOOL_RESOURCE_FORMS["runtime_scene_manage"]["journal_list"] is None
    assert MANAGE_TOOL_RESOURCE_FORMS["runtime_scene_manage"]["journal_get"] is None


def test_runtime_scene_domain_can_be_excluded():
    server = create_server(ws_port=0, exclude_domains={"runtime_scene"})

    assert "runtime_scene_manage" not in _tool_names(server)
```

- [ ] **Step 2: Run registration tests and verify they fail**

Run:

```powershell
uv run pytest tests/unit/test_runtime_scene_tool_registration.py -q
```

Expected: fail because `runtime_scene_manage` is not registered.

- [ ] **Step 3: Add tool registration**

Create `src/runtime_studio/tools/runtime_scene.py`:

```python
"""MCP tools for persistent runtime scene mutation."""

from __future__ import annotations

from fastmcp import FastMCP

from runtime_studio.handlers import runtime_scene as runtime_scene_handlers
from runtime_studio.tools._meta_tool import register_manage_tool

_DESCRIPTION = """\
Persistent runtime scene mutation.

These ops target the running game and immediately persist supported scene
structure changes into the Godot project. Successful mutating operations create
git commits.

Ops:
  - spawn_node(parent_path, name="", scene_path="", node_type="", properties={},
               children=[], transform={})
        Spawn a PackedScene instance or raw ClassDB node in the running game,
        persist the matching source scene change, journal it, and commit it.
  - remove_node(path)
        Remove a runtime node when it maps unambiguously to persisted scene
        state, journal the removal, and commit it.
  - journal_list()
        List recent Runtime Studio journal entries.
  - journal_get(path)
        Read one Runtime Studio journal entry by project-local journal path.
"""


async def _journal_list(runtime):
    state = await runtime.send_command("get_editor_state")
    return {"entries": [], "project_path": state.get("project_path", "")}


async def _journal_get(runtime, path: str):
    state = await runtime.send_command("get_editor_state")
    return {"path": path, "project_path": state.get("project_path", "")}


def register_runtime_scene_tools(mcp: FastMCP) -> None:
    register_manage_tool(
        mcp,
        tool_name="runtime_scene_manage",
        description=_DESCRIPTION,
        ops={
            "spawn_node": runtime_scene_handlers.spawn_node,
            "remove_node": runtime_scene_handlers.remove_node,
            "journal_list": _journal_list,
            "journal_get": _journal_get,
        },
        read_resource_forms={
            "journal_list": None,
            "journal_get": None,
        },
    )
```

Modify `src/runtime_studio/server.py` imports:

```python
from runtime_studio.tools.runtime_scene import register_runtime_scene_tools
```

Modify the instructions domain list in `src/runtime_studio/server.py` to include:

```text
  runtime_scene_manage spawn_node, remove_node, journal_list, journal_get
```

Modify registration near the other domain rollups:

```python
    if "runtime_scene" not in exclude:
        register_runtime_scene_tools(mcp)
```

Modify `src/runtime_studio/tools/domains.py` by adding a non-core domain entry:

```python
"runtime_scene": {"runtime_scene_manage"},
```

Use the existing structure in that file; preserve registration-order comments.

- [ ] **Step 4: Run registration tests and domain tests**

Run:

```powershell
uv run pytest tests/unit/test_runtime_scene_tool_registration.py tests/unit/test_tool_domains.py tests/unit/test_resource_form_lint.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/runtime_studio/tools/runtime_scene.py src/runtime_studio/server.py src/runtime_studio/tools/domains.py tests/unit/test_runtime_scene_tool_registration.py
git commit -m "Register runtime scene MCP tool"
```

---

### Task 5: Game Runtime Spawn/Remove Commands

**Files:**
- Modify: `plugin/addons/runtime_studio/runtime/game_helper.gd`
- Test: `tests/unit/test_runtime_scene_gdscript_static.py`

- [ ] **Step 1: Write failing source-level GDScript tests**

Create `tests/unit/test_runtime_scene_gdscript_static.py`:

```python
from __future__ import annotations

from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "plugin" / "addons" / "runtime_studio"


def test_game_helper_registers_runtime_spawn_and_remove_ops():
    source = (PLUGIN_ROOT / "runtime" / "game_helper.gd").read_text(encoding="utf-8")

    assert '"spawn_node":' in source
    assert "result = _game_spawn_node(json.data)" in source
    assert '"remove_node":' in source
    assert "result = _game_remove_node(json.data)" in source


def test_game_helper_constructs_inline_resources_safely():
    source = (PLUGIN_ROOT / "runtime" / "game_helper.gd").read_text(encoding="utf-8")

    assert "func _construct_inline_resource" in source
    assert 'ClassDB.is_parent_class(class_name, "Resource")' in source
    assert 'return {"state": "unsupported_persistence"' in source
```

- [ ] **Step 2: Run static tests and verify they fail**

Run:

```powershell
uv run pytest tests/unit/test_runtime_scene_gdscript_static.py -q
```

Expected: fail because the new game-helper ops are missing.

- [ ] **Step 3: Add game helper ops**

Modify `plugin/addons/runtime_studio/runtime/game_helper.gd` in `_handle_game_command`:

```gdscript
		"spawn_node":
			result = _game_spawn_node(json.data)
		"remove_node":
			result = _game_remove_node(json.data)
```

Add these functions near the existing `_game_get_node_info` helpers:

```gdscript
func _game_spawn_node(params: Dictionary) -> Dictionary:
	var parent := _resolve_runtime_node(str(params.get("parent_path", "")))
	if parent == null:
		return {"state": "unsupported_persistence", "error": "Runtime parent not found"}

	var node_result := _instantiate_runtime_node_tree(params)
	if node_result.has("error"):
		return node_result
	var node: Node = node_result.node
	if str(params.get("name", "")) != "":
		node.name = str(params.get("name", ""))
	parent.add_child(node, true)
	node.set_meta("_runtime_studio_operation_id", str(params.get("operation_id", "")))
	_apply_runtime_transform(node, params.get("transform", {}))
	return {
		"state": "spawned",
		"runtime_path": _runtime_path(node),
		"name": node.name,
		"type": node.get_class(),
		"operation_id": str(params.get("operation_id", "")),
	}


func _game_remove_node(params: Dictionary) -> Dictionary:
	var node := _resolve_runtime_node(str(params.get("path", "")))
	if node == null:
		return {"state": "unsupported_persistence", "error": "Runtime node not found"}
	if node == get_tree().current_scene:
		return {"state": "unsupported_persistence", "error": "Cannot remove current scene root"}
	var path := _runtime_path(node)
	node.get_parent().remove_child(node)
	node.queue_free()
	return {"state": "removed", "runtime_path": path}


func _instantiate_runtime_node_tree(params: Dictionary) -> Dictionary:
	var node: Node = null
	var scene_path := str(params.get("scene_path", ""))
	if scene_path != "":
		var packed := load(scene_path)
		if packed == null or not packed is PackedScene:
			return {"state": "unsupported_persistence", "error": "Scene not found or not PackedScene: %s" % scene_path}
		node = packed.instantiate()
	else:
		var node_type := str(params.get("node_type", ""))
		if node_type == "" or not ClassDB.class_exists(node_type) or not ClassDB.is_parent_class(node_type, "Node"):
			return {"state": "unsupported_persistence", "error": "Unsupported node_type: %s" % node_type}
		node = ClassDB.instantiate(node_type)
	if node == null:
		return {"state": "unsupported_persistence", "error": "Failed to instantiate node"}
	var property_result := _apply_runtime_properties(node, params.get("properties", {}))
	if property_result.has("error"):
		node.queue_free()
		return property_result
	for child_spec in params.get("children", []):
		if not child_spec is Dictionary:
			node.queue_free()
			return {"state": "unsupported_persistence", "error": "children entries must be objects"}
		var child_result := _instantiate_runtime_node_tree(child_spec)
		if child_result.has("error"):
			node.queue_free()
			return child_result
		var child: Node = child_result.node
		if str(child_spec.get("name", "")) != "":
			child.name = str(child_spec.get("name", ""))
		node.add_child(child, true)
	return {"node": node}


func _apply_runtime_properties(node: Node, properties: Dictionary) -> Dictionary:
	for key in properties.keys():
		var value = properties[key]
		if value is Dictionary and value.has("__class__"):
			var resource_result := _construct_inline_resource(value)
			if resource_result.has("error"):
				return resource_result
			value = resource_result.resource
		node.set(str(key), value)
	return {}


func _construct_inline_resource(spec: Dictionary) -> Dictionary:
	var class_name := str(spec.get("__class__", ""))
	if class_name == "" or not ClassDB.class_exists(class_name):
		return {"state": "unsupported_persistence", "error": "Unknown resource class: %s" % class_name}
	if not ClassDB.is_parent_class(class_name, "Resource"):
		return {"state": "unsupported_persistence", "error": "%s is not a Resource" % class_name}
	var resource: Resource = ClassDB.instantiate(class_name)
	if resource == null:
		return {"state": "unsupported_persistence", "error": "Failed to instantiate resource: %s" % class_name}
	for key in spec.keys():
		if str(key) == "__class__":
			continue
		resource.set(str(key), spec[key])
	return {"resource": resource}


func _apply_runtime_transform(node: Node, transform_spec: Dictionary) -> void:
	if transform_spec.is_empty():
		return
	if node is Node3D and transform_spec.has("origin"):
		var origin: Dictionary = transform_spec.get("origin", {})
		node.position = Vector3(float(origin.get("x", 0.0)), float(origin.get("y", 0.0)), float(origin.get("z", 0.0)))
	elif node is Node2D and transform_spec.has("position"):
		var pos: Dictionary = transform_spec.get("position", {})
		node.position = Vector2(float(pos.get("x", 0.0)), float(pos.get("y", 0.0)))
```

- [ ] **Step 4: Run static tests and Godot import**

Run:

```powershell
uv run pytest tests/unit/test_runtime_scene_gdscript_static.py -q
& "C:\Program Files\Godot\Godot_v4.6.3-stable_win64.exe\Godot_v4.6.3-stable_win64_console.exe" --headless --path test_project --import
```

Expected: pytest passes; Godot import completes without parse errors.

- [ ] **Step 5: Commit**

```powershell
git add plugin/addons/runtime_studio/runtime/game_helper.gd tests/unit/test_runtime_scene_gdscript_static.py
git commit -m "Add runtime game spawn and remove ops"
```

---

### Task 6: Editor Persistence Handler

**Files:**
- Create: `plugin/addons/runtime_studio/handlers/runtime_scene_handler.gd`
- Modify: `plugin/addons/runtime_studio/plugin.gd`
- Modify: `tests/unit/test_runtime_scene_gdscript_static.py`

- [ ] **Step 1: Extend static tests**

Append to `tests/unit/test_runtime_scene_gdscript_static.py`:

```python
def test_editor_runtime_scene_handler_exposes_resolution_and_persistence_commands():
    source = (PLUGIN_ROOT / "handlers" / "runtime_scene_handler.gd").read_text(encoding="utf-8")

    assert "func resolve_spawn" in source
    assert "func persist_spawn" in source
    assert "func resolve_remove" in source
    assert "func persist_remove" in source
    assert '"intent_required"' in source
    assert "EditorInterface.save_scene" in source


def test_plugin_registers_runtime_scene_editor_commands():
    source = (PLUGIN_ROOT / "plugin.gd").read_text(encoding="utf-8")

    assert "RuntimeSceneHandler" in source
    assert '_dispatcher.register("runtime_scene_resolve_spawn"' in source
    assert '_dispatcher.register("runtime_scene_persist_spawn"' in source
    assert '_dispatcher.register("runtime_scene_resolve_remove"' in source
    assert '_dispatcher.register("runtime_scene_persist_remove"' in source
```

- [ ] **Step 2: Run static tests and verify they fail**

Run:

```powershell
uv run pytest tests/unit/test_runtime_scene_gdscript_static.py -q
```

Expected: fail because `runtime_scene_handler.gd` and registrations do not exist.

- [ ] **Step 3: Add editor handler skeleton with safe v1 behavior**

Create `plugin/addons/runtime_studio/handlers/runtime_scene_handler.gd`:

```gdscript
@tool
extends RefCounted

const ErrorCodes := preload("res://addons/runtime_studio/utils/error_codes.gd")

var _undo_redo: EditorUndoRedoManager


func _init(undo_redo: EditorUndoRedoManager) -> void:
	_undo_redo = undo_redo


func resolve_spawn(params: Dictionary) -> Dictionary:
	var parent_path := str(params.get("parent_path", ""))
	var scene_root := EditorInterface.get_edited_scene_root()
	if scene_root == null:
		return {"data": {"state": "unsupported_persistence", "message": "No edited scene root"}}
	var parent := _resolve_scene_node(parent_path, scene_root)
	if parent == null:
		return {"data": {"state": "unsupported_persistence", "message": "Parent does not map to the edited scene", "parent_path": parent_path}}
	if parent.owner != null and parent.owner != scene_root:
		return {"data": {
			"state": "intent_required",
			"message": "The target parent belongs to a nested scene. Choose whether to modify this scene instance or the reusable nested scene.",
			"candidate_scene_paths": [scene_root.scene_file_path, parent.owner.scene_file_path],
		}}
	return {"data": {
		"state": "resolved",
		"scene_file": scene_root.scene_file_path,
		"parent_editor_path": _scene_path(parent, scene_root),
		"candidate_scene_paths": [scene_root.scene_file_path],
	}}


func persist_spawn(params: Dictionary) -> Dictionary:
	var request: Dictionary = params.get("request", {})
	var scene_root := EditorInterface.get_edited_scene_root()
	if scene_root == null:
		return {"data": {"state": "unsupported_persistence", "message": "No edited scene root"}}
	var parent := _resolve_scene_node(str(request.get("parent_path", "")), scene_root)
	if parent == null:
		return {"data": {"state": "unsupported_persistence", "message": "Parent does not map to the edited scene"}}
	var node_result := _instantiate_editor_node_tree(request, scene_root)
	if node_result.has("error"):
		return node_result
	var node: Node = node_result.node
	if str(request.get("name", "")) != "":
		node.name = str(request.get("name", ""))
	_undo_redo.create_action("Runtime Studio: Persist Spawn %s" % node.name)
	_undo_redo.add_do_method(parent, "add_child", node, true)
	_undo_redo.add_do_method(node, "set_owner", scene_root)
	_undo_redo.add_do_reference(node)
	_undo_redo.add_undo_method(parent, "remove_child", node)
	_undo_redo.commit_action()
	var err := EditorInterface.save_scene()
	if err != OK:
		return {"data": {"state": "partial_failure", "message": "Failed to save scene: %s" % error_string(err)}}
	return {"data": {"state": "persisted", "path": _scene_path(node, scene_root), "saved_files": [scene_root.scene_file_path]}}


func resolve_remove(params: Dictionary) -> Dictionary:
	var path := str(params.get("path", ""))
	var scene_root := EditorInterface.get_edited_scene_root()
	if scene_root == null:
		return {"data": {"state": "unsupported_persistence", "message": "No edited scene root"}}
	var node := _resolve_scene_node(path, scene_root)
	if node == null:
		return {"data": {"state": "unsupported_persistence", "message": "Node does not map to edited scene"}}
	if node == scene_root:
		return {"data": {"state": "unsupported_persistence", "message": "Cannot remove scene root"}}
	if node.owner != scene_root:
		return {"data": {"state": "intent_required", "message": "The node belongs to a nested scene.", "candidate_scene_paths": [scene_root.scene_file_path, node.owner.scene_file_path]}}
	return {"data": {"state": "resolved", "scene_file": scene_root.scene_file_path, "path": _scene_path(node, scene_root)}}


func persist_remove(params: Dictionary) -> Dictionary:
	var request: Dictionary = params.get("request", {})
	var scene_root := EditorInterface.get_edited_scene_root()
	var node := _resolve_scene_node(str(request.get("path", "")), scene_root)
	if node == null:
		return {"data": {"state": "unsupported_persistence", "message": "Node does not map to edited scene"}}
	var parent := node.get_parent()
	var path := _scene_path(node, scene_root)
	_undo_redo.create_action("Runtime Studio: Persist Remove %s" % node.name)
	_undo_redo.add_do_method(parent, "remove_child", node)
	_undo_redo.add_undo_method(parent, "add_child", node, true)
	_undo_redo.add_undo_reference(node)
	_undo_redo.commit_action()
	var err := EditorInterface.save_scene()
	if err != OK:
		return {"data": {"state": "partial_failure", "message": "Failed to save scene: %s" % error_string(err)}}
	return {"data": {"state": "persisted", "path": path, "saved_files": [scene_root.scene_file_path]}}


func _resolve_scene_node(path: String, scene_root: Node) -> Node:
	if scene_root == null:
		return null
	var scene_path := path.trim_prefix("/")
	if scene_path == str(scene_root.name):
		return scene_root
	var prefix := str(scene_root.name) + "/"
	if scene_path.begins_with(prefix):
		scene_path = scene_path.substr(prefix.length())
	return scene_root.get_node_or_null(scene_path)


func _scene_path(node: Node, scene_root: Node) -> String:
	if node == scene_root:
		return "/" + str(scene_root.name)
	return "/" + str(scene_root.name) + "/" + str(scene_root.get_path_to(node))


func _instantiate_editor_node_tree(params: Dictionary, scene_root: Node) -> Dictionary:
	var node: Node = null
	var scene_path := str(params.get("scene_path", ""))
	if scene_path != "":
		var packed := ResourceLoader.load(scene_path)
		if packed == null or not packed is PackedScene:
			return ErrorCodes.make(ErrorCodes.RESOURCE_NOT_FOUND, "Scene not found or not PackedScene: %s" % scene_path)
		node = packed.instantiate(PackedScene.GEN_EDIT_STATE_INSTANCE)
	else:
		var node_type := str(params.get("node_type", ""))
		if node_type == "" or not ClassDB.class_exists(node_type) or not ClassDB.is_parent_class(node_type, "Node"):
			return ErrorCodes.make(ErrorCodes.VALUE_OUT_OF_RANGE, "Unsupported node_type: %s" % node_type)
		node = ClassDB.instantiate(node_type)
	if node == null:
		return ErrorCodes.make(ErrorCodes.INTERNAL_ERROR, "Failed to instantiate node")
	_apply_editor_properties(node, params.get("properties", {}))
	for child_spec in params.get("children", []):
		var child_result := _instantiate_editor_node_tree(child_spec, scene_root)
		if child_result.has("error"):
			node.queue_free()
			return child_result
		var child: Node = child_result.node
		if str(child_spec.get("name", "")) != "":
			child.name = str(child_spec.get("name", ""))
		node.add_child(child, true)
		child.owner = scene_root
	return {"node": node}


func _apply_editor_properties(node: Node, properties: Dictionary) -> void:
	for key in properties.keys():
		var value = properties[key]
		if value is Dictionary and value.has("__class__"):
			value = _construct_inline_resource(value)
		node.set(str(key), value)


func _construct_inline_resource(spec: Dictionary) -> Resource:
	var class_name := str(spec.get("__class__", ""))
	if class_name == "" or not ClassDB.class_exists(class_name) or not ClassDB.is_parent_class(class_name, "Resource"):
		return null
	var resource: Resource = ClassDB.instantiate(class_name)
	for key in spec.keys():
		if str(key) == "__class__":
			continue
		resource.set(str(key), spec[key])
	return resource
```

Modify `plugin/addons/runtime_studio/plugin.gd`:

Add preload near the other handlers:

```gdscript
const RuntimeSceneHandler := preload("res://addons/runtime_studio/handlers/runtime_scene_handler.gd")
```

Instantiate near the other handler construction:

```gdscript
	var runtime_scene_handler := RuntimeSceneHandler.new(get_undo_redo())
```

Register commands near `game_command`:

```gdscript
	_dispatcher.register("runtime_scene_resolve_spawn", runtime_scene_handler.resolve_spawn)
	_dispatcher.register("runtime_scene_persist_spawn", runtime_scene_handler.persist_spawn)
	_dispatcher.register("runtime_scene_resolve_remove", runtime_scene_handler.resolve_remove)
	_dispatcher.register("runtime_scene_persist_remove", runtime_scene_handler.persist_remove)
```

- [ ] **Step 4: Run static tests and Godot import**

Run:

```powershell
uv run pytest tests/unit/test_runtime_scene_gdscript_static.py -q
& "C:\Program Files\Godot\Godot_v4.6.3-stable_win64.exe\Godot_v4.6.3-stable_win64_console.exe" --headless --path test_project --import
```

Expected: pytest passes; Godot import completes without parse errors.

- [ ] **Step 5: Commit**

```powershell
git add plugin/addons/runtime_studio/handlers/runtime_scene_handler.gd plugin/addons/runtime_studio/plugin.gd tests/unit/test_runtime_scene_gdscript_static.py
git commit -m "Persist runtime scene mutations in editor"
```

---

### Task 7: Journal Read Operations

**Files:**
- Modify: `src/runtime_studio/runtime_scene/journal.py`
- Modify: `src/runtime_studio/tools/runtime_scene.py`
- Test: `tests/unit/test_runtime_scene_git_journal.py`

- [ ] **Step 1: Add failing journal read tests**

Append to `tests/unit/test_runtime_scene_git_journal.py`:

```python
from runtime_studio.runtime_scene.journal import list_journal_entries, read_journal_entry


def test_list_and_read_journal_entries(tmp_path):
    written = write_journal_entry(
        tmp_path,
        {"operation_id": "op-456", "operation": "remove_node", "state": "persisted"},
    )

    entries = list_journal_entries(tmp_path)
    assert entries[0]["path"] == str(written.path)
    assert entries[0]["operation"] == "remove_node"

    loaded = read_journal_entry(tmp_path, str(written.path))
    assert loaded["operation_id"] == "op-456"
```

- [ ] **Step 2: Run journal tests and verify they fail**

Run:

```powershell
uv run pytest tests/unit/test_runtime_scene_git_journal.py -q
```

Expected: fail because `list_journal_entries` and `read_journal_entry` do not exist.

- [ ] **Step 3: Implement journal reads**

Append to `src/runtime_studio/runtime_scene/journal.py`:

```python
def list_journal_entries(project_root: str | Path, limit: int = 25) -> list[dict[str, Any]]:
    root = Path(project_root)
    journal_dir = root / ".runtime-studio" / "journal"
    if not journal_dir.exists():
        return []
    entries: list[dict[str, Any]] = []
    for path in sorted(journal_dir.glob("*.json"), reverse=True)[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        entries.append(
            {
                "path": str(path),
                "operation_id": data.get("operation_id", ""),
                "operation": data.get("operation", ""),
                "state": data.get("state", ""),
            }
        )
    return entries


def read_journal_entry(project_root: str | Path, path: str) -> dict[str, Any]:
    root = Path(project_root).resolve()
    target = Path(path)
    if not target.is_absolute():
        target = root / target
    target = target.resolve()
    journal_root = (root / ".runtime-studio" / "journal").resolve()
    if journal_root not in target.parents:
        raise ValueError("journal path must be inside .runtime-studio/journal")
    return json.loads(target.read_text(encoding="utf-8"))
```

Update `src/runtime_studio/tools/runtime_scene.py` imports and journal ops:

```python
from runtime_studio.runtime_scene.journal import list_journal_entries, read_journal_entry
```

```python
async def _journal_list(runtime, limit: int = 25):
    state = await runtime.send_command("get_editor_state")
    project_root = state.get("project_path", "")
    return {"entries": list_journal_entries(project_root, limit=limit)}


async def _journal_get(runtime, path: str):
    state = await runtime.send_command("get_editor_state")
    project_root = state.get("project_path", "")
    return read_journal_entry(project_root, path)
```

- [ ] **Step 4: Run journal/tool tests**

Run:

```powershell
uv run pytest tests/unit/test_runtime_scene_git_journal.py tests/unit/test_runtime_scene_tool_registration.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add src/runtime_studio/runtime_scene/journal.py src/runtime_studio/tools/runtime_scene.py tests/unit/test_runtime_scene_git_journal.py
git commit -m "Add runtime scene journal read operations"
```

---

### Task 8: Full Verification And README Update

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-05-27-runtime-spawn-remove-persistence-design.md` when implementation behavior differs from the design spec.

- [ ] **Step 1: Update README current status**

Modify the near-term roadmap in `README.md` so the first milestone notes the implemented tool:

```markdown
- `runtime_scene_manage` for runtime spawn/remove transactions
- hybrid `PackedScene` and raw ClassDB node spawning
- nested resource construction for supported Resource properties
- automatic scene save, journal entry, and git commits
```

- [ ] **Step 2: Run focused tests**

Run:

```powershell
uv run pytest `
  tests/unit/test_runtime_scene_schema.py `
  tests/unit/test_runtime_scene_git_journal.py `
  tests/unit/test_runtime_scene_handlers.py `
  tests/unit/test_runtime_scene_tool_registration.py `
  tests/unit/test_runtime_scene_gdscript_static.py `
  tests/unit/test_tool_domains.py `
  tests/unit/test_resource_form_lint.py -q
```

Expected: all focused tests pass.

- [ ] **Step 3: Run Python lint**

Run:

```powershell
uv run ruff check src tests
```

Expected: no ruff violations.

- [ ] **Step 4: Run full pytest**

Run:

```powershell
uv run pytest
```

Expected: all existing tests pass.

- [ ] **Step 5: Run Godot import validation**

Run:

```powershell
& "C:\Program Files\Godot\Godot_v4.6.3-stable_win64.exe\Godot_v4.6.3-stable_win64_console.exe" --headless --path test_project --import
```

Expected: import completes without GDScript parse/load errors.

- [ ] **Step 6: Run live restart-survival smoke test**

Start the editor against the test project:

```powershell
& "C:\Program Files\Godot\Godot_v4.6.3-stable_win64.exe\Godot_v4.6.3-stable_win64_console.exe" --path test_project
```

In the editor, run the current scene, then call the MCP tool from the connected
client:

```json
{
  "op": "spawn_node",
  "params": {
    "parent_path": "/Main",
    "name": "RuntimeStudioSmokeBox",
    "node_type": "StaticBody3D",
    "children": [
      {
        "name": "Mesh",
        "node_type": "MeshInstance3D",
        "properties": {
          "mesh": {
            "__class__": "BoxMesh",
            "size": {"x": 1.0, "y": 1.0, "z": 1.0}
          }
        }
      }
    ],
    "transform": {
      "origin": {"x": 0.0, "y": 1.0, "z": 0.0}
    }
  }
}
```

Expected MCP result:

```json
{
  "state": "persisted",
  "operation": "spawn_node",
  "runtime_result": {"state": "spawned"},
  "persistence_result": {"state": "persisted"}
}
```

Stop the game, run it again, then call:

```json
{
  "op": "get_scene_tree",
  "params": {
    "root_path": "/Main",
    "depth": 5
  }
}
```

Expected: one returned node has `"name": "RuntimeStudioSmokeBox"`.

- [ ] **Step 7: Commit verification docs**

```powershell
git add README.md docs/superpowers/specs/2026-05-27-runtime-spawn-remove-persistence-design.md
git commit -m "Document runtime scene transaction tool"
```

If the spec file did not change, run:

```powershell
git add README.md
git commit -m "Document runtime scene transaction tool"
```

---

## Self-Review Notes

- Spec coverage: this plan covers hybrid spawn, remove, nested resource construction, ambiguous nested-scene refusal, preflight commit, final commit, journal writes, journal reads, and restart-survival verification via saved scene/import tests.
- Boundaries: Python owns schema, git, journal, and transaction flow; game helper owns live mutation; editor handler owns source-scene persistence.
- Main risk: editor/runtime path mapping is intentionally conservative in v1. Ambiguous nested-scene cases return `intent_required` before live mutation.
- Testing: early tasks are unit-testable without Godot. Later tasks add static GDScript checks plus headless Godot import; a true play-from-editor integration test remains a follow-up once the transaction surface is stable.
