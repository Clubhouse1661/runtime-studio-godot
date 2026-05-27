# Runtime Studio for Godot

Connect AI assistants to a live Godot editor via the [Model Context Protocol](https://modelcontextprotocol.io/introduction) (MCP).

Runtime Studio for Godot bridges Claude Code, Codex, Antigravity, and other MCP clients with your editor — inspect scenes, create nodes, modify properties, run tests, search project files, and more, all from a prompt.

## Quick Start

1. Copy `addons/runtime_studio/` into your project's `addons/` folder
2. Enable the plugin: **Project > Project Settings > Plugins > Runtime Studio for Godot**
3. Pick your MCP client in the **Runtime Studio for Godot** dock and press **Configure**

The plugin auto-starts the MCP server and connects over WebSocket. No manual configuration required.

## Requirements

- Godot 4.3+ (4.4+ recommended)
- [uv](https://docs.astral.sh/uv/) (used to install the Python server)
  <details>
  <summary>Install uv</summary>

  **macOS / Linux:**
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

  **Windows (PowerShell):**
  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

  **Homebrew (macOS / Linux):**
  ```bash
  brew install uv
  ```

  **pipx:**
  ```bash
  pipx install uv
  ```

  See the [uv install docs](https://docs.astral.sh/uv/getting-started/installation/) for more options.

  </details>
- An MCP client ([Claude Code](https://docs.anthropic.com/en/docs/claude-code) | [Codex](https://openai.com/index/codex/) | [Antigravity](https://www.antigravity.dev/))

## Documentation

Full documentation, contributing guide, and source code: [github.com/Clubhouse1661/runtime-studio-godot](https://github.com/Clubhouse1661/runtime-studio-godot)

## License

[MIT](LICENSE)
