# Runtime Studio for Godot

Runtime Studio for Godot is an experimental fork of Godot AI exploring a
different way to make games:

**the running game is the primary development environment.**

Instead of treating play mode as the final check after editing scenes and
scripts, Runtime Studio aims to let a developer play the game while an AI agent
observes, edits, tests, and persists changes from inside the live runtime.

The long-term goal is a tight loop:

1. Play the game.
2. Ask the agent to change what you are seeing or feeling.
3. Apply the change immediately in the running game.
4. Persist the change back into scenes, resources, scripts, or data files.
5. Keep playing.
6. Use git as the history, review, and rollback layer.

This is deliberately more radical than a normal editor automation plugin. The
project is about live-first game development: tuning movement while moving,
building encounters while testing them, editing UI while interacting with it,
and turning runtime experiments into durable project changes.

## Current Status

This repository is at the beginning of that fork.

The current codebase still contains the mature editor/MCP foundation inherited
from Godot AI:

- a Godot editor plugin
- a Python FastMCP server
- client configuration helpers
- editor-side scene, node, resource, script, UI, audio, animation, material,
  camera, input map, filesystem, test, and logging tools
- runtime inspection and input tools under `game_manage`

The new Runtime Studio direction starts from that foundation, but the intended
center of gravity is different. Future work should bias toward runtime tools,
automatic persistence, live playtesting, change journals, and recovery through
git checkpoints.

## Core Idea

Runtime Studio treats every agent change as a live operation with a persistence
contract.

For example, a future runtime property edit should not merely do this:

```text
set /Main/Player speed = 8.5 in the running game
```

It should aim to do this:

```text
1. Record the current runtime value.
2. Change the running game immediately.
3. Map the runtime node back to its source scene/resource/script.
4. Persist the same change to the project.
5. Save the changed asset.
6. Record a structured journal entry.
7. Leave a git diff or checkpoint the developer can inspect or revert.
```

Some changes can be persisted automatically. Some will need to become reviewable
recipes when runtime-to-source mapping is ambiguous. The tool should make that
distinction explicit instead of silently saving bad state.

## Design Principles

- **Runtime first:** if a change can be tried live, try it live.
- **Persistence by default:** runtime changes should become project changes
  unless the tool says why they cannot.
- **Git is the safety net:** frequent diffs or checkpoints should make bold
  experimentation reversible.
- **Prefer structured operations:** property edits, scene instancing, node
  creation, signal wiring, data updates, and script patches should be recorded
  as machine-readable changes.
- **Fail loudly on ambiguity:** if the tool cannot safely map a runtime object
  to source, it should stop or produce a recipe rather than guess.
- **Avoid saving transient state by accident:** health, velocity, timers,
  random runtime children, and other live simulation state should not become
  permanent unless explicitly treated as design data.

## Near-Term Roadmap

The first useful milestone is a narrow vertical slice:

- runtime `set_property`
- runtime-to-editor source mapping for simple scene nodes
- immediate save of the matching scene or resource
- structured change journal entry
- git diff/checkpoint after the operation
- restart-and-verify workflow proving the change survived

After that, likely next steps are:

- runtime scene instancing with persistence
- runtime node creation/removal with persistence rules
- runtime UI element inspection
- runtime node search by group/class
- runtime debug draw and raycast tools
- explicit "persisted", "runtime-only", and "recipe-required" result states
- agent-facing tools for reviewing and reverting recent runtime changes

## Installation From Source

This fork is not currently packaged as an Asset Library release. Use a source
checkout.

Requirements:

- Godot 4.3+; 4.4+ recommended
- [uv](https://docs.astral.sh/uv/)
- an MCP client such as Codex, Claude Code, Claude Desktop, or similar

Clone the repository:

```bash
git clone https://github.com/Clubhouse1661/runtime-studio-godot.git
```

Copy the addon into a Godot project:

```bash
cp -r runtime-studio-godot/plugin/addons/runtime_studio your-project/addons/
```

Then enable **Runtime Studio for Godot** in:

```text
Project > Project Settings > Plugins
```

The plugin starts or adopts the Python MCP server and exposes the MCP endpoint:

```text
http://127.0.0.1:8000/mcp
```

The dock can configure supported clients automatically. Manual client config
can point at the same URL using the server name `runtime-studio-godot`.

## Development Setup

For work on this repository:

```powershell
.\script\setup-dev.ps1
```

On macOS/Linux:

```bash
script/setup-dev
```

This creates the local Python environment and links:

```text
test_project/addons/runtime_studio -> plugin/addons/runtime_studio
```

Useful checks:

```bash
uv run ruff check src tests
uv run pytest
python -m runtime_studio --version
```

On Windows, Godot import validation can be run with the installed console
binary, for example:

```powershell
& "C:\Program Files\Godot\Godot_v4.6.3-stable_win64.exe\Godot_v4.6.3-stable_win64_console.exe" --headless --path test_project --import
```

## Relationship To Godot AI

Runtime Studio for Godot began as a fork of
[hi-godot/godot-ai](https://github.com/hi-godot/godot-ai). The original project
is a broad, production-grade MCP toolset for controlling the Godot editor.

This fork is intended to become a more experimental project focused on live
runtime co-development and automatic persistence. Upstream credit and the MIT
license are preserved.

## License

MIT. See [LICENSE](LICENSE).
