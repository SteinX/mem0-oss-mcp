from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "plugins" / "mem0-oss" / "scripts" / "install_opencode_plugin.py"
ADAPTER = REPO_ROOT / "plugins" / "mem0-oss" / "scripts" / "oss_adapter" / "mem0_oss_memory_client.ts"
CAPTURE_POLICY = (
    REPO_ROOT
    / "plugins"
    / "mem0-oss"
    / "scripts"
    / "oss_adapter"
    / "opencode_capture_policy.ts"
)


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def make_upstream_opencode_fixture(root: Path) -> Path:
    plugin_root = root / "integrations" / "mem0-plugin" / ".opencode-plugin"
    write_json(
        plugin_root / "package.json",
        {
            "name": "@mem0/opencode-plugin",
            "version": "0.2.1",
            "type": "module",
            "description": "Official fixture",
            "main": "dist/index.js",
            "scripts": {"build": "bun build opencode-mem0.ts --outdir dist --target bun --format esm"},
            "dependencies": {"@opencode-ai/plugin": "^1.0.162", "mem0ai": "^3.0.8"},
        },
    )
    (plugin_root / "opencode-mem0.ts").write_text(
        """import type {Plugin} from "@opencode-ai/plugin";
import {MemoryClient} from "mem0ai";
import {homedir} from "os";
import {join} from "path";

const Mem0Plugin: Plugin = async (ctx) => {
  const {$, client} = ctx;
  void $;
  void client;
  const apiKey = process.env.MEM0_API_KEY;
  if (!apiKey) return {};
  const mem0 = new MemoryClient({apiKey});
  const userId = "u";
  const appId = "a";
  const branch = "main";
  const stats = {adds: 0};
  const sessionId = "session-fixture";
  const mem0StateDir = join(homedir(), ".mem0");
  const msgCount = 3;
  const safeText = "fixture capture text";
  const hasRemember = false;
  await mem0.add([{role: "user", content: "hello"}], {user_id: "u", app_id: "a"});

    if (msgCount % 3 === 0) {
      Promise.resolve().then(async () => {
        try {
          await mem0.add([{role: "user", content: safeText}], {
            user_id: userId,
            app_id: appId,
            metadata: {
              type: "auto_capture",
              source: "opencode",
              confidence: 0.7,
              session_id: sessionId,
              branch,
            },
            infer: true,
          } as any);
          stats.adds++;
        } catch {
        }
      });
    }

  return {};
};

export default Mem0Plugin;
""",
        encoding="utf-8",
    )
    skill = plugin_root / "opencode-skills" / "mem0-status" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text("---\nname: mem0-status\n---\n# Status\n", encoding="utf-8")
    return root


def test_opencode_installer_generates_oss_plugin_copy(tmp_path: Path) -> None:
    target_root = tmp_path / "opencode-plugins"
    env_file = tmp_path / "bridge.env"
    env_file.write_text("MEM0_EXAMPLE_TOKEN=test-token\n", encoding="utf-8")
    upstream_root = make_upstream_opencode_fixture(tmp_path / "mem0-upstream")

    result = subprocess.run(
        [
            sys.executable,
            str(INSTALLER),
            "--url",
            "https://mem0.example.test:18443/mcp",
            "--name",
            "mem0-example",
            "--display-name",
            "Mem0 Example",
            "--token-env-var",
            "MEM0_EXAMPLE_TOKEN",
            "--target-root",
            str(target_root),
            "--upstream-plugin-dir",
            str(upstream_root),
            "--env-file",
            str(env_file),
            "--no-build",
        ],
        text=True,
        capture_output=True,
        check=True,
    )

    assert "Generated OpenCode plugin:" in result.stdout
    plugin_root = target_root / "mem0-example"
    assert (plugin_root / "mem0_oss_memory_client.ts").is_file()
    assert (plugin_root / "opencode_capture_policy.ts").is_file()
    assert (plugin_root / "opencode-skills" / "mem0-status" / "SKILL.md").is_file()

    package = json.loads((plugin_root / "package.json").read_text())
    assert package["name"] == "@mem0-oss/mem0-example-opencode-plugin"
    assert package["version"].startswith("0.2.1+oss.")
    assert package["private"] is True
    assert "mem0ai" not in package["dependencies"]

    source = (plugin_root / "opencode-mem0.ts").read_text()
    assert 'from "./mem0_oss_memory_client"' in source
    assert 'from "./opencode_capture_policy"' in source
    assert 'from "mem0ai"' not in source
    assert 'url: "https://mem0.example.test:18443/mcp"' in source
    assert 'tokenEnvVar: "MEM0_EXAMPLE_TOKEN"' in source
    assert f'envFile: "{env_file}"' in source
    assert 'autoCaptureMode: "explicit"' in source
    assert "clientDream: false" in source
    assert "CapturePolicy.fromEnv" in source
    assert "capturePolicy.shouldCapture" in source
    assert "capturePolicy.recordCapture" in source
    assert "if (msgCount % 3 === 0)" not in source
    assert 'content: "hello"' in source
    assert "const apiKey = process.env.MEM0_API_KEY" in source


def test_opencode_installer_rejects_upstream_auto_capture_drift(tmp_path: Path) -> None:
    target_root = tmp_path / "opencode-plugins"
    upstream_root = make_upstream_opencode_fixture(tmp_path / "mem0-upstream")
    upstream_source = (
        upstream_root / "integrations" / "mem0-plugin" / ".opencode-plugin" / "opencode-mem0.ts"
    )
    upstream_source.write_text(
        upstream_source.read_text(encoding="utf-8").replace(
            "if (msgCount % 3 === 0)", "if (msgCount % 4 === 0)"
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(INSTALLER),
            "--url",
            "https://mem0.example.test:18443/mcp",
            "--target-root",
            str(target_root),
            "--upstream-plugin-dir",
            str(upstream_root),
            "--no-build",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "periodic auto-capture block" in result.stderr


def test_opencode_installer_writes_private_env_file_from_token(tmp_path: Path) -> None:
    target_root = tmp_path / "opencode-plugins"
    upstream_root = make_upstream_opencode_fixture(tmp_path / "mem0-upstream")
    token = "secret'token # value"

    result = subprocess.run(
        [
            sys.executable,
            str(INSTALLER),
            "--url",
            "https://mem0.example.test:18443/mcp",
            "--name",
            "mem0-example",
            "--token-env-var",
            "MEM0_EXAMPLE_TOKEN",
            "--token",
            token,
            "--target-root",
            str(target_root),
            "--upstream-plugin-dir",
            str(upstream_root),
            "--no-build",
        ],
        text=True,
        capture_output=True,
        check=True,
    )

    assert token not in result.stdout
    env_file = target_root / "env" / "mem0-example.env"
    assert env_file.read_text(encoding="utf-8") == f"MEM0_EXAMPLE_TOKEN={shlex.quote(token)}\n"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600

    source = (target_root / "mem0-example" / "opencode-mem0.ts").read_text()
    assert token not in source
    assert 'url: "https://mem0.example.test:18443/mcp"' in source
    assert 'tokenEnvVar: "MEM0_EXAMPLE_TOKEN"' in source
    assert f'envFile: "{env_file}"' in source


def test_opencode_installer_tightens_existing_token_env_file(tmp_path: Path) -> None:
    target_root = tmp_path / "opencode-plugins"
    env_file = tmp_path / "bridge.env"
    env_file.write_text("KEEP=value\nMEM0_EXAMPLE_TOKEN=old-token\n", encoding="utf-8")
    env_file.chmod(0o644)
    upstream_root = make_upstream_opencode_fixture(tmp_path / "mem0-upstream")

    subprocess.run(
        [
            sys.executable,
            str(INSTALLER),
            "--url",
            "https://mem0.example.test:18443/mcp",
            "--name",
            "mem0-example",
            "--token-env-var",
            "MEM0_EXAMPLE_TOKEN",
            "--token",
            "new-token",
            "--target-root",
            str(target_root),
            "--upstream-plugin-dir",
            str(upstream_root),
            "--env-file",
            str(env_file),
            "--no-build",
        ],
        text=True,
        capture_output=True,
        check=True,
    )

    assert env_file.read_text(encoding="utf-8") == "KEEP=value\nMEM0_EXAMPLE_TOKEN=new-token\n"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_opencode_adapter_routes_memory_client_calls_to_mcp(tmp_path: Path) -> None:
    if not shutil.which("bun"):
        pytest.skip("bun is required for this test")

    script = tmp_path / "adapter_test.ts"
    script.write_text(
        """
import {initializeMem0OssEnv, MemoryClient} from "__ADAPTER_URI__";

const calls: any[] = [];
(globalThis as any).fetch = async (url: string, options: any) => {
  const body = JSON.parse(options.body);
  calls.push({
    url,
    auth: options.headers.Authorization,
    name: body.params.name,
    args: body.params.arguments,
  });
  const payloads: Record<string, any> = {
    add_memory: {event_id: "evt_1", status: "SUCCEEDED"},
    search_memories: {results: [{id: "mem_1", memory: "hello"}]},
    get_event_status: {event_id: "evt_1", status: "SUCCEEDED"},
  };
  return new Response(JSON.stringify({
    jsonrpc: "2.0",
    id: body.id,
    result: {content: [{type: "text", text: JSON.stringify(payloads[body.params.name] ?? {ok: true})}]},
  }), {status: 200, headers: {"Content-Type": "application/json"}});
};

delete process.env.MEM0_API_KEY;
delete process.env.MEM0_OSS_AUTO_CAPTURE_MODE;
delete process.env.MEM0_DREAM;
process.env.MEM0_OSS_MCP_URL = "https://bridge.example/mcp/";
process.env.MEM0_OSS_MCP_TOKEN = "secret-token";

initializeMem0OssEnv({tokenEnvVar: "MEM0_OSS_MCP_TOKEN"});
if (process.env.MEM0_API_KEY !== "secret-token") {
  throw new Error(`MEM0_API_KEY was not initialized: ${process.env.MEM0_API_KEY}`);
}
if (process.env.MEM0_OSS_AUTO_CAPTURE_MODE !== "explicit") {
  throw new Error(`auto capture did not default explicit: ${process.env.MEM0_OSS_AUTO_CAPTURE_MODE}`);
}
if (process.env.MEM0_DREAM !== "false") {
  throw new Error(`client dream was not disabled: ${process.env.MEM0_DREAM}`);
}

process.env.MEM0_OSS_AUTO_CAPTURE_MODE = "bounded";
process.env.MEM0_DREAM = "true";
initializeMem0OssEnv({autoCaptureMode: "explicit", clientDream: false});
if (process.env.MEM0_OSS_AUTO_CAPTURE_MODE !== "bounded" || process.env.MEM0_DREAM !== "true") {
  throw new Error("explicit environment overrides were overwritten");
}

const envFile = "__ENV_FILE__";
await Bun.write(envFile, `MEM0_OSS_MCP_TOKEN='abc'"'"'def # token'\n`);
delete process.env.MEM0_API_KEY;
delete process.env.MEM0_OSS_MCP_TOKEN;
delete process.env.MEM0_OSS_ENV_FILE;
initializeMem0OssEnv({tokenEnvVar: "MEM0_OSS_MCP_TOKEN", envFile});
if (process.env.MEM0_API_KEY !== "abc'def # token") {
  throw new Error(`dotenv token was not round-tripped: ${process.env.MEM0_API_KEY}`);
}

const client = new MemoryClient({apiKey: process.env.MEM0_API_KEY!});
const add = await client.add([{role: "user", content: "hello"}], {userId: "root", appId: "mem0"});
const search = await client.search("hello", {topK: 3, filters: {AND: [{user_id: "root"}]}});
const event = await client.client.get("/v1/event/evt_1/");

if (add.event_id !== "evt_1") throw new Error("add result mismatch");
if (search.results[0].id !== "mem_1") throw new Error("search result mismatch");
if (event.data.status !== "SUCCEEDED") throw new Error("event result mismatch");
if (calls[0].url !== "https://bridge.example/mcp") throw new Error(`bad url ${calls[0].url}`);
if (calls.some((call) => call.auth !== "Bearer abc'def # token")) throw new Error("missing bearer token");
if (calls[0].name !== "add_memory") throw new Error(`bad first tool ${calls[0].name}`);
if (calls[0].args.user_id !== "root" || calls[0].args.app_id !== "mem0") {
  throw new Error(`identity args not normalized: ${JSON.stringify(calls[0].args)}`);
}
if (calls[1].name !== "search_memories" || calls[1].args.top_k !== 3) {
  throw new Error(`search args not normalized: ${JSON.stringify(calls[1])}`);
}
if (calls[2].name !== "get_event_status" || calls[2].args.event_id !== "evt_1") {
  throw new Error(`event args not mapped: ${JSON.stringify(calls[2])}`);
}
""".replace("__ADAPTER_URI__", ADAPTER.as_uri()).replace("__ENV_FILE__", (tmp_path / "bridge.env").as_posix()),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("MEM0_API_KEY", None)
    env.pop("MEM0_OSS_MCP_TOKEN", None)
    env.pop("MEM0_OSS_MCP_URL", None)
    result = subprocess.run(["bun", str(script)], text=True, capture_output=True, check=False, env=env)
    assert result.returncode == 0, result.stderr + result.stdout


def test_opencode_capture_policy_defaults_explicit_and_bounds_opt_in_capture(
    tmp_path: Path,
) -> None:
    if not shutil.which("bun"):
        pytest.skip("bun is required for this test")

    script = tmp_path / "capture_policy_test.ts"
    script.write_text(
        """
import {statSync} from "node:fs";
import {CapturePolicy} from "__CAPTURE_POLICY_URI__";

const now = new Date("2026-07-23T12:00:00Z");
const explicit = CapturePolicy.fromEnv({}, "__EXPLICIT_STATE__", "repo", "session-explicit");
if (explicit.mode !== "explicit") throw new Error(`default mode was ${explicit.mode}`);
if (explicit.shouldCapture({messageCount: 10, text: "durable fact", explicitRemember: false, now}).capture) {
  throw new Error("explicit mode captured a periodic message");
}
const remember = explicit.shouldCapture({
  messageCount: 10,
  text: "remember this durable fact",
  explicitRemember: true,
  now,
});
if (remember.capture || remember.reason !== "explicit_tool_required") {
  throw new Error(`explicit remember bypassed the tool path: ${JSON.stringify(remember)}`);
}

const bounded = CapturePolicy.fromEnv(
  {MEM0_OSS_AUTO_CAPTURE_MODE: "bounded"},
  "__BOUNDED_STATE__",
  "repo",
  "session-bounded",
);
if (bounded.shouldCapture({messageCount: 9, text: "first fact", explicitRemember: false, now}).capture) {
  throw new Error("bounded mode captured before message ten");
}
const first = bounded.shouldCapture({messageCount: 10, text: "Ｆｉｘ   cache", explicitRemember: false, now});
if (!first.capture || first.text !== "Ｆｉｘ   cache") {
  throw new Error(`bounded mode rejected eligible capture: ${JSON.stringify(first)}`);
}
bounded.recordCapture(first.text!, now);
const duplicate = bounded.shouldCapture({messageCount: 20, text: "Fix cache", explicitRemember: false, now});
if (duplicate.capture || duplicate.reason !== "duplicate") {
  throw new Error(`normalized duplicate was not suppressed: ${JSON.stringify(duplicate)}`);
}
for (const [messageCount, text] of [[20, "second fact"], [30, "third fact"]] as const) {
  const result = bounded.shouldCapture({messageCount, text, explicitRemember: false, now});
  if (!result.capture) throw new Error(`eligible session capture rejected: ${JSON.stringify(result)}`);
  bounded.recordCapture(text, now);
}
const sessionLimit = bounded.shouldCapture({messageCount: 40, text: "fourth fact", explicitRemember: false, now});
if (sessionLimit.capture || sessionLimit.reason !== "session_limit") {
  throw new Error(`session limit not enforced: ${JSON.stringify(sessionLimit)}`);
}
const oversized = bounded.shouldCapture({messageCount: 50, text: "x".repeat(2001), explicitRemember: false, now});
if (oversized.capture || oversized.reason !== "input_too_large") {
  throw new Error(`oversized input was not rejected: ${JSON.stringify(oversized)}`);
}
if ((statSync("__BOUNDED_STATE__/opencode-capture-state.json").mode & 0o777) !== 0o600) {
  throw new Error("capture state file is not owner-only");
}

for (let index = 0; index < 20; index += 1) {
  const daily = CapturePolicy.fromEnv(
    {MEM0_OSS_AUTO_CAPTURE_MODE: "bounded"},
    "__DAILY_STATE__",
    "repo",
    `daily-session-${index}`,
  );
  const text = `daily fact ${index}`;
  const result = daily.shouldCapture({messageCount: 10, text, explicitRemember: false, now});
  if (!result.capture) throw new Error(`daily capture ${index} rejected: ${JSON.stringify(result)}`);
  daily.recordCapture(text, now);
}
const dailyBlocked = CapturePolicy.fromEnv(
  {MEM0_OSS_AUTO_CAPTURE_MODE: "bounded"},
  "__DAILY_STATE__",
  "repo",
  "daily-session-overflow",
).shouldCapture({messageCount: 10, text: "daily overflow", explicitRemember: false, now});
if (dailyBlocked.capture || dailyBlocked.reason !== "daily_limit") {
  throw new Error(`daily limit not enforced: ${JSON.stringify(dailyBlocked)}`);
}

const legacy = CapturePolicy.fromEnv(
  {MEM0_OSS_AUTO_CAPTURE_MODE: "legacy"},
  "__LEGACY_STATE__",
  "repo",
  "legacy-session",
);
if (!legacy.shouldCapture({messageCount: 3, text: "legacy fact", explicitRemember: false, now}).capture) {
  throw new Error("legacy mode did not capture message three");
}
if (legacy.shouldCapture({messageCount: 4, text: "legacy fact two", explicitRemember: false, now}).capture) {
  throw new Error("legacy mode captured message four");
}

await Bun.write("__INVALID_STATE__/opencode-capture-state.json", "not-json");
const invalid = CapturePolicy.fromEnv(
  {MEM0_OSS_AUTO_CAPTURE_MODE: "bounded"},
  "__INVALID_STATE__",
  "repo",
  "invalid-session",
);
if (invalid.mode !== "explicit") throw new Error("invalid state did not fail closed");
"""
        .replace("__CAPTURE_POLICY_URI__", CAPTURE_POLICY.as_uri())
        .replace("__EXPLICIT_STATE__", (tmp_path / "explicit").as_posix())
        .replace("__BOUNDED_STATE__", (tmp_path / "bounded").as_posix())
        .replace("__DAILY_STATE__", (tmp_path / "daily").as_posix())
        .replace("__LEGACY_STATE__", (tmp_path / "legacy").as_posix())
        .replace("__INVALID_STATE__", (tmp_path / "invalid").as_posix()),
        encoding="utf-8",
    )
    (tmp_path / "invalid").mkdir()

    result = subprocess.run(
        ["bun", str(script)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
