# Runtime Spawn/Remove Persistence Design

## Goal

Runtime Studio should let an agent spawn and remove nodes while the game is
running, persist those changes to the Godot project immediately, and commit the
result to git.

The first milestone is intentionally focused on scene structure changes:

- spawn a `PackedScene` instance from a `res://...` scene path
- spawn a raw ClassDB node type
- apply initial properties during spawn
- construct nested resources inline for supported resource properties
- remove nodes that can be mapped back to persisted scene state
- create a preflight git checkpoint when the worktree is dirty
- create a final git commit for every successful persisted operation

## Non-Goals

- Free-form script editing from the runtime transaction tool.
- Persisting arbitrary simulation state such as velocity, health, cooldowns, or
  timers.
- Guessing where to save when a runtime node belongs to a nested scene and the
  user's intent is unclear.
- Supporting exported game builds. This workflow is for play-from-editor.

## User Model

The developer is playing the game and asks the agent for structural changes,
for example:

```text
Add a torch beside the cave entrance.
Spawn this enemy camp here.
Remove that temporary wall.
Add a MeshInstance3D with a BoxMesh and a collision shape.
```

The developer expects the change to appear immediately in the running game and
to survive a restart without manually replaying editor steps.

## MCP Surface

Add a new focused meta-tool named `runtime_scene_manage`. Keeping these
operations separate from `game_manage` makes the larger transaction and
persistence contract visible to the agent.

Initial operations:

- `spawn_node`
- `remove_node`
- `journal_list`
- `journal_get`

`spawn_node` accepts either `scene_path` or `node_type`.

Example:

```json
{
  "op": "spawn_node",
  "parent_path": "/Main/Level/Props",
  "name": "Torch",
  "scene_path": "res://props/torch.tscn",
  "transform": {
    "origin": {"x": 12.0, "y": 0.0, "z": -4.5},
    "basis": "identity"
  }
}
```

Raw node example with nested resource construction:

```json
{
  "op": "spawn_node",
  "parent_path": "/Main/Level/Blockers",
  "name": "CrateBlocker",
  "node_type": "StaticBody3D",
  "children": [
    {
      "name": "Mesh",
      "node_type": "MeshInstance3D",
      "properties": {
        "mesh": {
          "__class__": "BoxMesh",
          "size": {"x": 2.0, "y": 1.0, "z": 2.0}
        }
      }
    },
    {
      "name": "Shape",
      "node_type": "CollisionShape3D",
      "properties": {
        "shape": {
          "__class__": "BoxShape3D",
          "size": {"x": 2.0, "y": 1.0, "z": 2.0}
        }
      }
    }
  ]
}
```

`remove_node` accepts a runtime path and removes the corresponding persisted
scene node when mapping is unambiguous.

## Transaction Contract

Every mutating operation performs basic schema validation first. Invalid tool
payloads return an error without creating git commits or touching the running
game.

After schema validation, every mutating operation follows one transaction:

1. Inspect git status in the Godot project root.
2. If there are uncommitted changes, commit all current changes first:
   `Runtime Studio checkpoint: before runtime change`.
3. Validate the requested runtime target and persistence target.
4. Apply the change in the running game.
5. Apply the equivalent change to the editor/source scene.
6. Save the changed scene/resource file.
7. Write a structured journal entry.
8. Commit all resulting changes with a generated message.
9. Return a result with runtime path, saved files, journal entry, commit hashes,
   and verification status.

If the preflight commit fails, no runtime change is attempted.

If the runtime change succeeds but persistence fails, the result must report a
partial failure and include enough detail for recovery. In v1, the preferred
recovery is to remove the runtime-only spawned node when possible and leave the
project files untouched after the preflight checkpoint.

## Persistence Rules

Runtime Studio may persist automatically when all of these are true:

- The parent runtime node maps to a currently editable source scene.
- The operation affects scene structure or explicitly supplied design data.
- The target scene file can be saved by the editor.
- The change can be represented with Godot's normal scene/resource serializer.

Runtime Studio must stop with `intent_required` when the parent belongs to a
nested instanced scene and there are multiple reasonable save targets, such as:

- save only an override in the currently running main scene
- open and modify the reusable nested scene file

The result should explain the choices in plain language and include the
candidate scene paths.

Runtime Studio must stop with `unsupported_persistence` when the operation can
be performed live but cannot yet be represented safely in saved project files.

## Architecture

### Python MCP Layer

The Python tool layer owns the public schema, transaction orchestration, git
commands, and result shaping.

Responsibilities:

- expose spawn/remove operations
- validate mutually exclusive `scene_path` and `node_type`
- call editor/game commands in order
- run git status and commits in the Godot project root
- write journal files
- return explicit success, partial failure, or refusal states

### Game Runtime Helper

The game helper owns immediate live mutation inside the running game process.

Responsibilities:

- resolve runtime paths against `get_tree().current_scene`
- instantiate `PackedScene` or ClassDB node types
- construct supported nested resources
- apply supplied properties and transforms
- tag runtime-created nodes with stable metadata
- remove runtime nodes by path or operation id
- return enough metadata to map the runtime node to source

### Editor Handler

The editor side owns source-scene mutation and saving.

Responsibilities:

- map runtime parent paths to edited scene paths
- create matching nodes using existing editor-side creation helpers where
  possible
- construct nested resources with the same supported schema as runtime
- set node owners correctly so nodes serialize
- save affected scenes/resources
- refuse ambiguous nested-scene ownership

### Journal

Each transaction writes a JSON entry under a project-local Runtime Studio
journal directory, for example:

```text
.runtime-studio/journal/2026-05-27T18-30-12Z-spawn-node.json
```

Entries include:

- operation id
- operation type
- request payload
- runtime result
- persistence result
- touched files
- preflight commit hash, if any
- final commit hash, if any
- refusal or partial-failure details

The journal directory is committed by Runtime Studio as part of the final
operation commit.

## Data Flow

Spawn:

1. MCP request arrives.
2. Python checks git state and creates checkpoint commit if needed.
3. Python asks editor to resolve the persistence target.
4. Python asks game helper to spawn the live node.
5. Python asks editor to create/save the equivalent source node.
6. Python writes the journal entry.
7. Python creates the final git commit.
8. Python returns operation metadata to the agent.

Remove:

1. MCP request arrives with runtime path.
2. Python checkpoints dirty worktree if needed.
3. Python asks editor/game layers to prove the node maps to persisted state.
4. Python removes the live node.
5. Python removes the source node and saves.
6. Python writes the journal entry.
7. Python creates the final git commit.

## Result States

Mutating operations return one of:

- `persisted`: live change applied, saved, journaled, and committed
- `intent_required`: no change applied because the save target is ambiguous
- `unsupported_persistence`: no change applied because the durable equivalent
  is not supported
- `preflight_failed`: no change applied because the checkpoint commit failed
- `partial_failure`: a live change happened but persistence, journaling, or git
  finalization failed

## Testing

Unit tests:

- schema validation rejects both `scene_path` and `node_type`
- schema validation rejects neither `scene_path` nor `node_type`
- nested resource construction accepts supported `Resource` subclasses
- nested resource construction rejects non-resource classes
- git preflight creates a checkpoint commit for a dirty worktree
- no runtime command is sent when preflight commit fails
- generated commit messages include operation type and target
- result states serialize consistently

Godot/plugin tests:

- runtime helper spawns a ClassDB node
- runtime helper spawns a `PackedScene`
- runtime helper applies transform and simple properties
- runtime helper constructs nested mesh/collision/material resources
- runtime helper removes a spawned node
- editor handler persists a matching raw node tree
- editor handler persists a matching `PackedScene` instance
- editor handler refuses ambiguous nested-scene targets

Integration test:

1. Start the test project from the editor.
2. Call spawn with a raw `StaticBody3D` tree and nested `BoxMesh`/`BoxShape3D`.
3. Verify the node exists in the running game.
4. Verify the scene file changed.
5. Verify a journal entry exists.
6. Verify a git commit exists.
7. Restart the game.
8. Verify the node still exists.

## Open Follow-Up Work

- Visual picking so the agent can target "that thing" from a screenshot.
- Runtime property editing with the same transaction contract.
- Scene-instance override editing for nested scenes.
- Revert tools that use journal entries and git commits.
- A UI panel for reviewing recent runtime transactions.
