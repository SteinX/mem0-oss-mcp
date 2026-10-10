from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_opencode_plugin_installer import INSTALLER, make_upstream_opencode_fixture


def modern_source(root: Path) -> Path:
    make_upstream_opencode_fixture(root)
    old = root / "integrations/mem0-plugin/.opencode-plugin"
    plugin = root / "integrations/opencode-plugin"
    old.rename(plugin)
    source = plugin / "opencode-mem0.ts"
    content = source.read_text(encoding="utf-8").replace(
        '  const mem0StateDir = join(homedir(), ".mem0");\n', ""
    )
    content = 'import {identity} from "../agent-plugin-core/typescript/src/identity.ts";\n' + content
    content = content.replace('  const apiKey = process.env.MEM0_API_KEY;', '  const apiKey = resolveApiKey(process.env);')
    content = content.replace('import {MemoryClient} from "mem0ai";', 'import {MemoryClient} from "mem0ai";\nimport {resolveApiKey} from "./api-key";')
    content = content.replace("  void $;", "  void $;\n  void identity;")
    source.write_text(content, encoding="utf-8")
    (plugin / "api-key.ts").write_text(
        "export function resolveApiKey(env: NodeJS.ProcessEnv) {return env.MEM0_API_KEY;}\n",
        encoding="utf-8",
    )
    core = root / "integrations/agent-plugin-core/typescript/src"
    core.mkdir(parents=True)
    (core / "identity.ts").write_text('export const identity = "fixture";\n', encoding="utf-8")
    (plugin / "project.ts").write_text(
        'export {identity} from "../agent-plugin-core/typescript/src/identity.ts";\n',
        encoding="utf-8",
    )
    return root


def generate(root: Path, target: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(INSTALLER), "--portable", "--no-build", "--upstream-plugin-dir", str(root), "--target-root", str(target), *extra],
        text=True,
        capture_output=True,
        check=False,
    )


def test_portable_generation_vendors_sibling_core_when_source_uses_new_layout(tmp_path: Path) -> None:
    # Given: the current source layout uses sibling shared modules and no old state anchor.
    upstream = modern_source(tmp_path / "upstream")
    target = tmp_path / "release"
    # When: a portable package is generated from the checkout root.
    result = generate(upstream, target)
    # Then: generated source resolves shared imports within the package and has no connection binding.
    assert result.returncode == 0, result.stderr
    plugin = target / "mem0-oss"
    source = (plugin / "opencode-mem0.ts").read_text(encoding="utf-8")
    assert (plugin / "agent-plugin-core/identity.ts").is_file()
    assert 'from "./agent-plugin-core/identity.ts"' in source
    assert 'from "./agent-plugin-core/identity.ts"' in (plugin / "project.ts").read_text(encoding="utf-8")
    assert 'from "./opencode_release_config"' in source
    assert "CapturePolicy.fromEnv" in source
    assert "resolveApiKey(process.env)" not in source
    assert 'autoCaptureMode: "explicit"' in source
    assert "clientDream: false" in source
    for path in plugin.rglob("*"):
        if path.is_file():
            assert str(tmp_path) not in path.read_text(encoding="utf-8")


def test_portable_generation_retains_legacy_source_layout(tmp_path: Path) -> None:
    # Given: a shipped upstream checkout still uses the old nested plugin layout.
    upstream = make_upstream_opencode_fixture(tmp_path / "upstream")
    # When: a portable package is generated without a URL.
    result = generate(upstream, tmp_path / "release")
    # Then: generation succeeds and emits the external config runtime.
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "release/mem0-oss/opencode_release_config.ts").is_file()


@pytest.mark.parametrize("options", [
    ["--url", "https://private.example/mcp"],
    ["--token", "secret-value"],
    ["--token-stdin"],
    ["--env-file", "/private/bridge.env"],
    ["--install"],
])
def test_portable_generation_rejects_connection_or_install_options(tmp_path: Path, options: list[str]) -> None:
    # Given: portable generation has a connection or local installation argument.
    # When: the generator parses it.
    result = generate(tmp_path / "absent-upstream", tmp_path / "release", *options)
    # Then: it rejects the option before accessing source or exposing the supplied value.
    assert result.returncode == 1
    assert "portable packages cannot" in result.stderr
    assert "secret-value" not in result.stderr + result.stdout


@pytest.mark.parametrize("runtime_override", [False, True])
def test_portable_runtime_loads_external_config_and_honors_environment(tmp_path: Path, runtime_override: bool) -> None:
    # Given: a portable package and an external connection JSON with a private token file.
    if not shutil.which("bun"):
        pytest.skip("bun is required for runtime verification")
    upstream = make_upstream_opencode_fixture(tmp_path / "upstream")
    generated = generate(upstream, tmp_path / "release")
    assert generated.returncode == 0, generated.stderr
    token_file = tmp_path / "token.env"
    token_file.write_text("FILE_TOKEN='config-secret'\nMEM0_API_KEY='dotenv-api-key'\n", encoding="utf-8")
    config = tmp_path / "home/.mem0-oss/clients/opencode/connection.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({
        "url": "https://config.example/mcp/", "env_file": str(token_file),
        "token_env_var": "FILE_TOKEN", "user_id": "config-user", "app_id": "config-app",
    }), encoding="utf-8")
    runtime = tmp_path / "release/mem0-oss/opencode_release_config.ts"
    script = tmp_path / "runtime.ts"
    script.write_text(f'''import {{initializeMem0OssEnv}} from {json.dumps(runtime.as_uri())};
initializeMem0OssEnv();
console.log(JSON.stringify({{
  url: process.env.MEM0_OSS_MCP_URL, token: process.env.MEM0_API_KEY,
  user: process.env.MEM0_USER_ID, app: process.env.MEM0_APP_ID,
  capture: process.env.MEM0_OSS_AUTO_CAPTURE_MODE, dream: process.env.MEM0_DREAM,
}}));
''', encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("MEM0_")}
    env["HOME"] = str(tmp_path / "home")
    if runtime_override:
        env.update({
            "MEM0_OSS_CLIENT_CONFIG": str(config), "MEM0_OSS_MCP_URL": "https://runtime.example/mcp",
            "MEM0_API_KEY": "runtime-secret", "MEM0_USER_ID": "runtime-user", "MEM0_APP_ID": "runtime-app",
        })
    # When: the copied runtime initializes in a fresh Bun process.
    result = subprocess.run(["bun", str(script)], text=True, capture_output=True, env=env, check=False)
    # Then: explicit environment values win, otherwise external values are used.
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert actual == {
        "url": f"https://{'runtime' if runtime_override else 'config'}.example/mcp",
        "token": "runtime-secret" if runtime_override else "config-secret",
        "user": "runtime-user" if runtime_override else "config-user",
        "app": "runtime-app" if runtime_override else "config-app",
        "capture": "explicit", "dream": "false",
    }


@pytest.mark.parametrize("config_text", ['{"url":"https://user:secret-value@example/mcp"}', '{"url": "secret-value",', '{"url":"https://example/mcp","token":"secret-value"}'])
def test_portable_runtime_rejects_invalid_config_without_exposing_values(tmp_path: Path, config_text: str) -> None:
    # Given: runtime config contains invalid JSON, a credential-bearing URL, or an inline token.
    if not shutil.which("bun"):
        pytest.skip("bun is required for runtime verification")
    generated = generate(make_upstream_opencode_fixture(tmp_path / "upstream"), tmp_path / "release")
    assert generated.returncode == 0, generated.stderr
    config = tmp_path / "connection.json"
    config.write_text(config_text, encoding="utf-8")
    runtime = tmp_path / "release/mem0-oss/opencode_release_config.ts"
    script = tmp_path / "invalid.ts"
    script.write_text(f'import {{initializeMem0OssEnv}} from {json.dumps(runtime.as_uri())}; initializeMem0OssEnv();\n', encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("MEM0_")}
    env["MEM0_OSS_CLIENT_CONFIG"] = str(config)
    # When: the runtime attempts to initialize.
    result = subprocess.run(["bun", str(script)], text=True, capture_output=True, env=env, check=False)
    # Then: startup fails with a sanitized configuration error.
    assert result.returncode != 0
    assert "OpenCode connection config" in result.stderr
    assert "secret-value" not in result.stderr + result.stdout
