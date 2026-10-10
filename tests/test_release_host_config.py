from __future__ import annotations

import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "plugins/mem0-oss/scripts")
)
from release_host import enable_hooks


@pytest.mark.parametrize(
    "header",
    [
        "[features]",
        "[features] # native feature flags",
        '[ "features" ] # flags',
        "  ['features']",
    ],
)
def test_enable_hooks_preserves_valid_features_tables(
    tmp_path: Path, header: str
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'model = "preserved-model"\n'
        + header
        + '\nmulti_agent = false\ncodex_hooks = false # keep this comment\n[other]\nvalue = "preserved-value"\n'
    )
    enable_hooks(path)
    configuration = tomllib.loads(path.read_text())
    assert configuration["model"] == "preserved-model"
    assert configuration["other"]["value"] == "preserved-value"
    assert configuration["features"] == {"multi_agent": False, "codex_hooks": True}
    assert "# keep this comment" in path.read_text()


def test_enable_hooks_handles_features_header_at_end_of_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('model = "preserved-model"\n[features]')
    enable_hooks(path)
    assert tomllib.loads(path.read_text())["features"]["codex_hooks"] is True
