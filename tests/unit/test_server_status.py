from pathlib import Path

from starlette.testclient import TestClient

import runtime_studio as _runtime_studio_pkg
from runtime_studio import __version__
from runtime_studio.server import create_server


def test_status_route_reports_live_server_version():
    server = create_server(ws_port=9555, exclude_domains={"audio", "theme"})
    app = server.http_app(transport="streamable-http")
    ## ``base_url`` overrides Starlette TestClient's default ``testserver``
    ## Host header. The DNS-rebinding guard (origin_guard.py) rejects any
    ## non-loopback Host, so without this the request 403s before
    ## reaching the status route. See audit-v2 finding #1 (#345).
    client = TestClient(app, base_url="http://127.0.0.1")

    response = client.get("/runtime-studio-godot/status")

    assert response.status_code == 200
    assert response.json() == {
        "name": "runtime-studio-godot",
        "server_version": __version__,
        "ws_port": 9555,
        "tool_surface": "rollup",
        "exclude_domains": ["audio", "theme"],
        "package_path": str(Path(_runtime_studio_pkg.__file__).resolve().parent),
    }


def test_status_route_package_path_points_at_loaded_package_dir():
    ## #416: the editor's "Incompatible server" banner consumes
    ## `package_path` so the user can tell which `src/runtime_studio/` is
    ## actually serving the port — critical in a multi-worktree setup
    ## where the root .venv may resolve to a different branch than the
    ## worktree the user is editing. Pin that the field is an absolute,
    ## resolved path to a real directory containing `__init__.py`.
    server = create_server(ws_port=9556)
    app = server.http_app(transport="streamable-http")
    client = TestClient(app, base_url="http://127.0.0.1")

    response = client.get("/runtime-studio-godot/status")

    assert response.status_code == 200
    payload = response.json()
    package_path = Path(payload["package_path"])
    assert package_path.is_absolute(), (
        "package_path must be absolute so the user can match it against ps/Get-Process output"
    )
    assert (package_path / "__init__.py").exists(), (
        "package_path must point at the actual loaded runtime_studio package dir"
    )
