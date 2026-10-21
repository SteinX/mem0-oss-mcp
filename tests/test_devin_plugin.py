from __future__ import annotations

import json
import os
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from mem0_oss_mcp import server as bridge

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "plugins/mem0-oss/scripts/install_devin_plugin.py"
UPSTREAM = ROOT / "third_party/mem0"
TOKEN = "fixture-devin-token"
TOKEN_VAR = "MEM0_DEVIN_TEST_TOKEN"


@pytest.fixture
def workspace(tmp_path):
    repo = tmp_path / "workspace with spaces"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "remote",
            "add",
            "origin",
            "https://example.test/team/project.git",
        ],
        check=True,
    )
    return repo


@contextmanager
def http_server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.fixture
def backend(monkeypatch):
    """Real bridge HTTP/REST mapping, with only LLM/vector persistence replaced."""
    calls = []
    memories = []

    class Core(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, body))
            if self.path == "/memories":
                memory = {
                    "id": f"memory-{len(memories)}",
                    "memory": "\n".join(item["content"] for item in body["messages"]),
                    "user_id": body["user_id"],
                    "agent_id": body.get("agent_id"),
                    "run_id": body.get("run_id"),
                    "metadata": body["metadata"],
                    "score": 0.9,
                }
                memories.append(memory)
                payload = {"results": [memory]}
            elif self.path == "/search":
                filters = body["filters"]
                payload = {
                    "results": [
                        item
                        for item in memories
                        if all(
                            item["metadata"].get(key) == value
                            if key == "app_id"
                            else item.get(key) == value
                            for key, value in filters.items()
                        )
                    ]
                }
            else:
                self.send_error(404)
                return
            raw = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    class QuietBridge(bridge.Handler):
        def log_message(self, *_args):
            pass

    with http_server(Core) as core_url:
        monkeypatch.setattr(bridge.Config, "base_url", core_url)
        monkeypatch.setattr(bridge.Config, "api_key", "fixture-core-key")
        monkeypatch.setattr(bridge.Config, "sidecar_base_url", "")
        monkeypatch.setattr(
            bridge.Config,
            "authenticator",
            bridge.McpAuthenticator.from_env(
                "",
                {
                    "MEM0_OSS_MCP_AUTH_MODE": "static",
                    "MEM0_OSS_MCP_TOKEN": TOKEN,
                },
            ),
        )
        with http_server(QuietBridge) as bridge_url:
            yield f"{bridge_url}/mcp", calls, memories


def clean_environment():
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("MEM0_", "PLUGIN_", "CLAUDE_PLUGIN_"))
        and key != "PYTHONPATH"
    }
    env["NO_PROXY"] = ",".join(
        filter(None, (env.get("NO_PROXY"), "127.0.0.1", "localhost"))
    )
    env["MEM0_CODE_IDLE_FLUSH_SECONDS"] = "0"
    return env


def generate(
    tmp_path, url="https://oss.example.test/mcp", *options, check=True, token=TOKEN
):
    if not (UPSTREAM / "integrations/mem0-agent-plugin/core/memory_core.py").is_file():
        pytest.skip("initialize third_party/mem0 to exercise the real upstream plugin")
    base = tmp_path / "plugins with spaces"
    result = subprocess.run(
        [
            sys.executable,
            str(INSTALLER),
            "--url",
            url,
            "--target-root",
            str(base),
            "--token-env-var",
            TOKEN_VAR,
            *(["--token-stdin"] if token is not None else []),
            "--user-id",
            "devin-user",
            *options,
        ],
        input=token + "\n" if token is not None else None,
        env=clean_environment(),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    if check:
        assert result.returncode == 0, result.stderr
    name = options[options.index("--name") + 1] if "--name" in options else "mem0-oss"
    return base / name, result


def cli(plugin, workspace, *args, env=None):
    return subprocess.run(
        [
            sys.executable,
            str(plugin / "core/memory_cli.py"),
            "--harness",
            "devin",
            *args,
        ],
        cwd=workspace,
        env=env or clean_environment(),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


def hook(plugin, workspace, action, payload=None, *args, env=None):
    environment = env or clean_environment()
    environment["DEVIN_PROJECT_DIR"] = str(workspace)
    events = {
        "session-start": "SessionStart",
        "user-prompt": "UserPromptSubmit",
        "post-tool": "PostToolUse",
        "stop": "Stop",
    }
    event = (
        ("PostCompaction" if "post-compaction" in args else "SessionEnd")
        if action == "flush"
        else events[action]
    )
    config = json.loads((plugin / "hooks.json").read_text())
    command = config["hooks"][event][0]["hooks"][0]["command"]
    result = subprocess.run(
        shlex.split(command),
        input=json.dumps({"session_id": "devin-session", **(payload or {})}),
        cwd=plugin,
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result


def rpc(plugin, method, params=None, env=None):
    servers = json.loads((plugin / ".mcp.json").read_text())["mcpServers"]
    config = next(iter(servers.values()))
    arguments = [arg.replace("${PLUGIN_ROOT}", str(plugin)) for arg in config["args"]]
    result = subprocess.run(
        [config["command"], *arguments],
        input=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": method,
                "params": params or {},
            }
        )
        + "\n",
        cwd=plugin,
        env=env or clean_environment(),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def tool(plugin, name, arguments, *, env=None):
    result = rpc(plugin, "tools/call", {"name": name, "arguments": arguments}, env=env)[
        "result"
    ]
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


def test_generated_plugin_loads_all_mcp_tools_and_saves_explicit_memory(
    tmp_path, workspace, backend
):
    url, calls, memories = backend
    plugin, result = generate(tmp_path, url)
    assert TOKEN not in result.stdout + result.stderr
    manifest = json.loads((plugin / ".devin-plugin/plugin.json").read_text())
    assert manifest["name"] == "mem0-oss"
    assert not (plugin / "mcp.json").exists()
    assert {item.name for item in (plugin / "skills").iterdir()} == {
        "remember",
        "search",
        "status",
        "pause",
        "resume",
        "forget",
    }
    assert all(
        TOKEN not in item.read_text() for item in plugin.rglob("*") if item.is_file()
    )
    env_file = plugin.parent / "env/mem0-oss.env"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    status = json.loads(cli(plugin, workspace, "status", "--json").stdout)
    assert status["user_id"] == "devin-user"
    assert status["app_id"] == "team-project"
    assert status["api_key_configured"] is True
    assert status["auto_capture"] is False and status["durable_writes"] is False
    assert (
        rpc(plugin, "initialize", {"protocolVersion": "2025-03-26"})["result"][
            "capabilities"
        ]["tools"]
        == {}
    )
    names = {item["name"] for item in rpc(plugin, "tools/list")["result"]["tools"]}
    assert names == {
        "add_memory",
        "search_memories",
        "get_memories",
        "get_memory",
        "update_memory",
        "delete_memory",
        "delete_all_memories",
        "delete_entities",
        "list_entities",
        "list_events",
        "get_event_status",
    }
    scope = {"user_id": status["user_id"], "app_id": status["app_id"]}
    added = tool(
        plugin,
        "add_memory",
        {**scope, "text": "Use pnpm for this repository", "infer": False},
    )
    event = tool(plugin, "get_event_status", {"event_id": added["event_id"]})
    assert event["status"] == "SUCCEEDED" and event["result_count"] == 1
    found = tool(
        plugin,
        "search_memories",
        {**scope, "query": "Which package manager?", "top_k": 3},
    )
    assert found["results"][0]["memory"] == "Use pnpm for this repository"
    assert calls[0][1]["infer"] is False
    assert memories[0]["metadata"]["app_id"] == scope["app_id"]
    assert cli(plugin, workspace, "doctor", "--json").returncode == 0


def test_distinct_plugins_coexist_and_keep_their_endpoints_and_credentials(tmp_path):
    requests = []

    class Endpoint(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, self.headers.get("Authorization")))
            raw = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps({"endpoint": self.path}),
                            }
                        ]
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    with http_server(Endpoint) as url:
        personal, _ = generate(tmp_path, f"{url}/personal/mcp")
        work, _ = generate(
            tmp_path,
            f"{url}/work/mcp",
            "--name",
            "mem0-work",
            token="fixture-work-token",
        )
        # Devin keeps the first source on a server-name collision. Include an
        # existing official declaration, then load both generated bundles.
        loaded = {"mem0": {"command": "official-mem0-server"}}
        for plugin in (personal, work):
            config = json.loads((plugin / ".mcp.json").read_text())["mcpServers"]
            for name, declaration in config.items():
                loaded.setdefault(name, declaration)
        assert set(loaded) == {"mem0", "mem0-oss", "mem0-work"}
        for plugin in (personal, work):
            for skill in ("remember", "search", "forget"):
                text = (plugin / "skills" / skill / "SKILL.md").read_text()
                assert f"mcp__{plugin.name}__" in text
                assert "{{SERVER_NAME}}" not in text
        environment = clean_environment()
        environment[TOKEN_VAR] = "unrelated-ambient-token"
        assert tool(
            personal, "search_memories", {"query": "package manager"}, env=environment
        ) == {"endpoint": "/personal/mcp"}
        assert tool(
            work, "search_memories", {"query": "package manager"}, env=environment
        ) == {"endpoint": "/work/mcp"}
        assert requests == [
            ("/personal/mcp", f"Bearer {TOKEN}"),
            ("/work/mcp", "Bearer fixture-work-token"),
        ]


def test_environment_credential_works_when_no_private_file_is_configured(
    tmp_path, backend
):
    url, calls, _ = backend
    plugin, _ = generate(tmp_path, url, token=None)
    assert "env_file" not in json.loads((plugin / ".mem0-oss-runtime.json").read_text())
    environment = clean_environment()
    environment[TOKEN_VAR] = TOKEN
    result = tool(
        plugin,
        "search_memories",
        {
            "query": "package manager",
            "user_id": "devin-user",
            "app_id": "team-project",
        },
        env=environment,
    )
    assert result == {"results": []}
    assert calls[-1][1]["filters"] == {
        "user_id": "devin-user",
        "app_id": "team-project",
    }


def test_devin_recall_uses_project_env_and_keeps_repositories_separate(
    tmp_path, workspace, backend
):
    url, calls, _ = backend
    plugin, _ = generate(tmp_path, url)
    tool(
        plugin,
        "add_memory",
        {
            "user_id": "devin-user",
            "app_id": "team-project",
            "text": "Use pnpm",
            "infer": False,
        },
    )
    hook(plugin, workspace, "session-start", {"source": "startup"})
    prompt = {"prompt": "Which package manager did we choose for this repository?"}
    output = hook(plugin, workspace, "user-prompt", prompt)
    injected = json.loads(output.stdout)["hookSpecificOutput"]
    assert injected["hookEventName"] == "UserPromptSubmit"
    assert "Use pnpm" in injected["additionalContext"]
    searches = [body for path, body in calls if path == "/search"]
    assert searches[0]["filters"] == {"user_id": "devin-user", "app_id": "team-project"}
    assert searches[0]["latest_only"] is True and searches[0]["rerank"] is False
    assert hook(plugin, workspace, "user-prompt", prompt).stdout == ""
    assert len([path for path, _ in calls if path == "/search"]) == 1
    other = tmp_path / "another repo"
    other.mkdir()
    subprocess.run(["git", "init", "--quiet", str(other)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(other),
            "remote",
            "add",
            "origin",
            "https://example.test/team/other.git",
        ],
        check=True,
    )
    assert (
        hook(
            plugin, other, "user-prompt", {**prompt, "session_id": "other-session"}
        ).stdout
        == ""
    )
    assert calls[-1][1]["filters"]["app_id"] == "team-other"


def test_native_tool_outcomes_and_direct_core_capture(tmp_path, workspace, backend):
    url, calls, memories = backend
    plugin, _ = generate(tmp_path, url, "--auto-capture")
    assert (
        json.loads(cli(plugin, workspace, "status", "--json").stdout)["auto_capture"]
        is True
    )
    hook(plugin, workspace, "session-start")
    hook(
        plugin,
        workspace,
        "user-prompt",
        {"prompt": "Please make the workspace use pnpm for package installation."},
    )
    hook(
        plugin,
        workspace,
        "post-tool",
        {
            "tool_name": "exec",
            "tool_input": {"command": "pnpm test"},
            "tool_response": {
                "success": False,
                "output": "",
                "error": "Missing test script",
            },
        },
    )
    hook(
        plugin,
        workspace,
        "post-tool",
        {
            "tool_name": "read",
            "tool_input": {"path": str(workspace / "package.json")},
            "tool_response": {"success": True, "output": "{}", "error": None},
        },
    )
    hook(
        plugin,
        workspace,
        "post-tool",
        {"tool_name": "unclassified", "tool_input": {}, "tool_response": {}},
    )
    hook(
        plugin,
        workspace,
        "stop",
        {"last_assistant_message": "The project now uses pnpm for installation."},
    )
    data = plugin.parent / "data/mem0-oss/evidence.sqlite3"
    with sqlite3.connect(data) as connection:
        rows = connection.execute(
            "SELECT kind, payload_json FROM events ORDER BY id"
        ).fetchall()
    outcomes = [
        (kind, json.loads(body))
        for kind, body in rows
        if kind in {"tool_failure", "tool_result"}
    ]
    assert outcomes[0][0] == "tool_failure" and outcomes[0][1]["failed"] is True
    assert outcomes[1][1]["path"] == str(workspace / "package.json")
    assert outcomes[2][1]["failed"] is None
    environment = clean_environment()
    environment["MEM0_CODE_SYNC_FLUSH"] = "1"
    flushed = hook(
        plugin, workspace, "flush", {}, "--reason", "post-compaction", env=environment
    )
    value = json.loads(flushed.stdout)
    assert value["status"] == "semantic-succeeded", value
    assert value["memory_count"] >= 1
    assert memories and all(item["run_id"] == "devin-session" for item in memories)
    assert all(item["metadata"]["source"] == "devin_plugin" for item in memories)
    assert any("pnpm" in item["memory"] for item in memories)
    count = len([path for path, _ in calls if path == "/memories"])
    hook(plugin, workspace, "flush", {}, "--reason", "session-end", env=environment)
    assert len([path for path, _ in calls if path == "/memories"]) == count


def test_missing_selected_credential_never_uses_hosted_or_cached_keys(
    tmp_path, workspace
):
    plugin, _ = generate(tmp_path)
    (plugin.parent / "env/mem0-oss.env").write_text("MEM0_API_KEY=hosted-dotenv-key\n")
    environment = clean_environment()
    environment.update(
        MEM0_API_KEY="hosted-process-key",
        MEM0_OSS_MCP_TOKEN="other-client-key",
        PLUGIN_OPTION_API_KEY="host-option-key",
    )
    cached = plugin.parent / "data/mem0-oss/api-key"
    cached.parent.mkdir(parents=True)
    cached.write_text("old-cached-key")
    assert not json.loads(
        cli(plugin, workspace, "status", "--json", env=environment).stdout
    )["api_key_configured"]
    response = rpc(plugin, "tools/list", env=environment)
    assert response["error"]["code"] == -32001
    assert "hosted" not in json.dumps(response)


def test_host_options_do_not_override_explicit_identity_or_create_key_cache(
    tmp_path, workspace, backend
):
    url, calls, _ = backend
    plugin, _ = generate(tmp_path, url)
    environment = clean_environment()
    environment.update(
        MEM0_API_KEY="hosted-process-key",
        PLUGIN_OPTION_API_KEY="host-option-key",
        CLAUDE_PLUGIN_OPTION_API_KEY="claude-host-option-key",
        PLUGIN_OPTION_USER_ID="other-host-user",
        CLAUDE_PLUGIN_OPTION_USER_ID="another-host-user",
    )
    hook(plugin, workspace, "session-start", env=environment)
    status = cli(plugin, workspace, "status", "--json", env=environment)
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["user_id"] == "devin-user"
    assert not (plugin.parent / "data/mem0-oss/api-key").exists()
    doctor = cli(plugin, workspace, "doctor", "--json", env=environment)
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert calls[-1][1]["filters"]["user_id"] == "devin-user"
    assert not (plugin.parent / "data/mem0-oss/api-key").exists()


def test_disabled_capture_keeps_pending_work_local_even_in_worker(
    tmp_path, workspace, backend
):
    url, calls, _ = backend
    plugin, _ = generate(tmp_path, url)
    hook(
        plugin,
        workspace,
        "user-prompt",
        {"prompt": "Remember that the repository uses pnpm for all installation."},
    )
    hook(
        plugin,
        workspace,
        "stop",
        {"last_assistant_message": "The repository uses pnpm."},
    )
    environment = clean_environment()
    environment["MEM0_CODE_SYNC_FLUSH"] = "1"
    assert (
        hook(
            plugin,
            workspace,
            "flush",
            {},
            "--reason",
            "post-compaction",
            env=environment,
        ).stdout
        == ""
    )
    assert all(path != "/memories" for path, _ in calls)
    pending = plugin.parent / "data/mem0-oss/pending/disposable.json"
    pending.parent.mkdir(parents=True)
    pending.write_text(
        json.dumps(
            {
                "hook_input": {"session_id": "devin-session", "cwd": str(workspace)},
                "reason": "session-end",
            }
        )
    )
    hook(plugin, workspace, "session-start")
    assert pending.is_file()
    running = pending.with_suffix(".running")
    pending.rename(running)
    worker = subprocess.run(
        [sys.executable, str(plugin / "core/flush_worker.py"), str(running)],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert json.loads(worker.stdout)["status"] == "paused"
    assert pending.is_file()
    assert all(path != "/memories" for path, _ in calls)


def test_paused_hook_does_not_search_or_extract_but_explicit_tools_work(
    tmp_path, workspace, backend
):
    url, calls, _ = backend
    plugin, _ = generate(tmp_path, url, "--auto-capture")
    assert cli(plugin, workspace, "pause").returncode == 0
    hook(
        plugin,
        workspace,
        "user-prompt",
        {"prompt": "What did we decide about pnpm in this repository?"},
    )
    hook(plugin, workspace, "flush", {}, "--reason", "session-end")
    assert calls == []
    assert tool(
        plugin,
        "search_memories",
        {"user_id": "devin-user", "app_id": "team-project", "query": "pnpm"},
    ) == {"results": []}
    assert cli(plugin, workspace, "resume").returncode == 0
    hook(
        plugin,
        workspace,
        "user-prompt",
        {"prompt": "What did we decide about pnpm in this repository?"},
    )
    assert len(calls) == 2


def test_durable_capture_retries_keep_the_same_sidecar_batch_key(
    tmp_path, workspace, backend, monkeypatch
):
    url, _, _ = backend
    plugin, _ = generate(tmp_path, url, "--auto-capture", "--durable-writes")
    hook(
        plugin,
        workspace,
        "user-prompt",
        {"prompt": "The repository uses pnpm for package installation."},
    )
    hook(
        plugin,
        workspace,
        "stop",
        {"last_assistant_message": "Package installation uses pnpm."},
    )
    monkeypatch.setattr(bridge.Config, "sidecar_base_url", "http://sidecar.fixture")
    monkeypatch.setattr(bridge.Config, "sidecar_api_key", "private-operator-fixture")
    keys = []

    def sidecar(method, path, body=None, *, query=None, idempotency_key=None):
        assert method == "POST" and path == "/v3/memories/add"
        keys.append(idempotency_key)
        if len(keys) == 1:
            raise bridge.BackendError(503, "fixture transient failure")
        principal = bridge.current_http_principal()
        return {
            "event": {
                "id": "devin-sidecar-event",
                "channel": {
                    "transport": "mcp",
                    "credential_kind": principal.credential_kind,
                    "credential_id": principal.credential_id,
                },
            },
            "memory": {"results": [{"id": "durable-memory"}]},
        }

    monkeypatch.setattr(bridge, "_sidecar_backend", sidecar)
    environment = clean_environment()
    environment["MEM0_CODE_SYNC_FLUSH"] = "1"
    first = hook(
        plugin, workspace, "flush", {}, "--reason", "post-compaction", env=environment
    )
    assert json.loads(first.stdout)["status"] == "error"
    second = hook(
        plugin, workspace, "flush", {}, "--reason", "post-compaction", env=environment
    )
    assert json.loads(second.stdout)["status"] == "semantic-succeeded", second.stdout
    assert len(keys) == 2 and keys[0] == keys[1] and keys[0].startswith("devin-")


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@oss.example/mcp",
        "https://oss.example/mcp?key=secret",
        "https://oss.example/mcp#secret",
        "file:///tmp/mcp",
    ],
)
def test_invalid_endpoint_is_rejected_before_credentials_are_written(tmp_path, url):
    plugin, result = generate(tmp_path, url, check=False)
    assert result.returncode != 0
    assert not plugin.parent.exists()
    assert "secret" not in result.stderr


@pytest.mark.parametrize("location", ["--env-file", "--data-dir"])
def test_credentials_and_evidence_cannot_live_inside_package(tmp_path, location):
    base = tmp_path / "plugins with spaces"
    plugin, result = generate(
        tmp_path,
        "https://oss.example/mcp",
        location,
        str(base / "mem0-oss/private"),
        check=False,
    )
    assert result.returncode != 0
    assert not plugin.exists()


def test_upstream_contract_failure_preserves_working_plugin_and_key(tmp_path):
    plugin, _ = generate(tmp_path)
    original = (plugin / ".mem0-oss-runtime.json").read_bytes()
    credential = plugin.parent / "env/mem0-oss.env"
    credential.write_text(f"{TOKEN_VAR}=previous-key\n")
    source = tmp_path / "upstream"
    for name in ("mem0-agent-plugin", "agent-plugin-core/python"):
        shutil.copytree(
            UPSTREAM / "integrations" / name, source / "integrations" / name
        )
    runtime = source / "integrations/mem0-agent-plugin/core/memory_core.py"
    runtime.write_text(
        runtime.read_text().replace(
            "def api_key() -> str:", "def changed_api_key_contract() -> str:"
        )
    )
    _, result = generate(
        tmp_path,
        "https://new.example/mcp",
        "--upstream-plugin-dir",
        str(source),
        check=False,
    )
    assert result.returncode != 0 and "contract changed" in result.stderr
    assert (plugin / ".mem0-oss-runtime.json").read_bytes() == original
    assert "previous-key" in credential.read_text()


def test_regeneration_preserves_evidence_and_refuses_unowned_directory(
    tmp_path, workspace
):
    plugin, _ = generate(tmp_path)
    hook(plugin, workspace, "session-start")
    status = json.loads(cli(plugin, workspace, "status", "--json").stdout)
    _, result = generate(tmp_path)
    assert not result.stderr
    assert not list(plugin.parent.glob(".mem0-oss-previous-*"))
    assert (
        json.loads(cli(plugin, workspace, "status", "--json").stdout)["events"]
        == status["events"]
        == 1
    )
    (plugin / ".mem0-oss-runtime.json").unlink()
    _, result = generate(tmp_path, check=False)
    assert result.returncode != 0 and "refusing to replace" in result.stderr
    assert (plugin / "hooks.json").is_file()


def test_readonly_backup_reports_recovery_without_reverting_update(tmp_path):
    plugin, _ = generate(tmp_path)
    (plugin / "core").chmod(0o555)
    next_token = "next-devin-fixture-token"
    _, result = generate(tmp_path, "https://new.example/mcp", token=next_token)
    backups = list(plugin.parent.glob(".mem0-oss-previous-*"))
    assert len(backups) == 1
    backup = backups[0]
    try:
        assert (backup / "core").is_dir()
        assert "plugin updated, but previous package cleanup failed" in result.stderr
        assert f"Backup remains at: {backup}" in result.stderr
        assert "directory permissions" in result.stderr
        retry = shlex.split(result.stderr.split("then retry cleanup with:\n")[1])
        assert retry[-1] == str(backup)
        assert TOKEN not in result.stderr and next_token not in result.stderr
        config = json.loads((plugin / ".mem0-oss-runtime.json").read_text())
        assert config["url"] == "https://new.example/mcp"
        credential = plugin.parent / "env/mem0-oss.env"
        assert next_token in credential.read_text()
        assert stat.S_IMODE(credential.stat().st_mode) == 0o600

        # The reported command works after the actual permission error is fixed.
        (backup / "core").chmod(0o755)
        subprocess.run(retry, check=True, capture_output=True, timeout=10)
        assert not backup.exists()
        assert (plugin / "hooks.json").is_file()
        assert next_token in credential.read_text()
    finally:
        if (backup / "core").exists():
            (backup / "core").chmod(0o755)


@pytest.mark.parametrize(
    "failure", ["move-old", "promote", "write-token", "new-token", "restore-old"]
)
def test_failed_promotion_restores_or_preserves_package_and_credential(
    tmp_path, monkeypatch, failure
):
    monkeypatch.syspath_prepend(str(INSTALLER.parent))
    import install_devin_plugin as installer

    target = tmp_path / "plugin"
    staging = tmp_path / "staging/plugin"
    backup = tmp_path / "previous"
    target.mkdir()
    staging.mkdir(parents=True)
    (target / "version").write_text("previous")
    (staging / "version").write_text("next")
    credential = tmp_path / "credential.env"
    previous = b"MEM0_DEVIN_TEST_TOKEN=previous-token\nOTHER_SETTING=keep\n"
    if failure != "new-token":
        credential.write_bytes(previous)
        credential.chmod(0o640)
    original_rename = Path.rename

    def rename(path, destination):
        if (
            (failure == "move-old" and path == target)
            or (failure in {"promote", "new-token", "restore-old"} and path == staging)
            or (failure == "restore-old" and path == backup)
        ):
            raise PermissionError("injected promotion failure")
        return original_rename(path, destination)

    def partial_write(path, *_args):
        path.write_text("partially-written-secret")
        path.chmod(0o600)
        raise OSError("injected credential write failure")

    monkeypatch.setattr(Path, "rename", rename)
    if failure == "write-token":
        monkeypatch.setattr(installer, "write_token_env_file", partial_write)
    with pytest.raises(
        OSError, match="preserved at" if failure == "restore-old" else "injected"
    ):
        installer.promote_plugin(
            staging,
            target,
            backup,
            env_file=credential,
            token_env_var=TOKEN_VAR,
            token="next-token",
        )
    assert (staging / "version").read_text() == "next"
    if failure == "restore-old":
        assert (backup / "version").read_text() == "previous"
        assert not target.exists()
    else:
        assert (target / "version").read_text() == "previous"
        assert not backup.exists()
    if failure == "new-token":
        assert not credential.exists()
    else:
        assert credential.read_bytes() == previous
        assert stat.S_IMODE(credential.stat().st_mode) == 0o640


def test_install_calls_only_devin_local_plugin_registration(tmp_path, monkeypatch):
    commands = tmp_path / "bin"
    commands.mkdir()
    devin = commands / "devin"
    arguments = tmp_path / "devin-args.json"
    devin.write_text(
        f"#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\nPath({str(arguments)!r}).write_text(json.dumps(sys.argv[1:]))\n"
    )
    devin.chmod(0o755)
    monkeypatch.setenv("PATH", str(commands) + os.pathsep + os.environ["PATH"])
    plugin, _ = generate(tmp_path, "https://oss.example/mcp", "--install")
    assert json.loads(arguments.read_text()) == [
        "plugins",
        "install",
        "--local",
        "--yes",
        str(plugin),
    ]
