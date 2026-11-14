from __future__ import annotations

from pathlib import Path

import pytest

import config.settings as settings_module
from config.settings import Settings


@pytest.fixture()
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config" / "config.toml"
    monkeypatch.setattr(settings_module, "config_file", lambda: path)
    monkeypatch.setattr("app.lifecycle.get_loaded_model", lambda *a, **k: None)
    monkeypatch.setenv("GEMMA_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.delenv("GEMMA_MODEL", raising=False)
    monkeypatch.delenv("GEMMA_WORKSPACE", raising=False)
    monkeypatch.delenv("GEMMA_PROVIDER", raising=False)
    monkeypatch.delenv("GEMMA_PERMISSION_MODE", raising=False)
    monkeypatch.delenv("GEMMA_CONTEXT_LIMIT", raising=False)
    monkeypatch.delenv("GEMMA_THEME", raising=False)
    return path


def test_save_load_roundtrip(config_path: Path) -> None:
    settings = Settings(
        model="custom-model",
        theme="nord",
        workspace_dir="/tmp/workspace",
        agent_max_steps=7,
        provider="native",
        context_limit=16_384,
        compact_threshold=0.75,
        compact_keep_tokens=2_000,
        permission_mode="ask",
        permission_rules=["bash *rm -rf * ask", "write *.env deny"],
        mcp_enabled=False,
        lsp_enabled=False,
        skills_enabled=False,
        skills_dirs=["/tmp/skills"],
    )
    settings.save_config()

    loaded = Settings.load()
    assert loaded.model == "custom-model"
    assert loaded.theme == "nord"
    assert loaded.workspace_dir == "/tmp/workspace"
    assert loaded.agent_max_steps == 7
    assert loaded.provider == "native"
    assert loaded.context_limit == 16_384
    assert loaded.compact_threshold == 0.75
    assert loaded.compact_keep_tokens == 2_000
    assert loaded.permission_mode == "ask"
    assert loaded.permission_rules == ["bash *rm -rf * ask", "write *.env deny"]
    assert loaded.mcp_enabled is False
    assert loaded.lsp_enabled is False
    assert loaded.skills_enabled is False
    assert loaded.skills_dirs == ["/tmp/skills"]


def test_save_preserves_unknown_keys(config_path: Path) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "# keep this section\n"
        "[custom]\n"
        "answer = 42\n"
        "flag = true\n"
        "\n"
        "[agent]\n"
        "enabled = true\n"
        "experimental = \"keep me\"\n",
        encoding="utf-8",
    )

    settings = Settings.load()
    settings.save_model("new-model")

    text = config_path.read_text(encoding="utf-8")
    reloaded = Settings.load()
    import tomllib

    data = tomllib.loads(text)
    assert data["custom"] == {"answer": 42, "flag": True}
    assert data["agent"]["experimental"] == "keep me"
    assert reloaded.model == "new-model"


def test_save_escapes_special_characters(config_path: Path) -> None:
    tricky = 'quote " backslash \\ newline\n tab\t done'
    settings = Settings(title=tricky, api_key='a"b\\c')
    settings.save_config()

    loaded = Settings.load()
    assert loaded.title == tricky
    assert loaded.api_key == 'a"b\\c'


def test_save_validates_provider_and_mode(config_path: Path) -> None:
    settings = Settings()
    with pytest.raises(ValueError):
        settings.save_provider("bogus")
    with pytest.raises(ValueError):
        settings.save_permission_mode("bogus")


def test_context_limit_is_clamped(config_path: Path) -> None:
    settings = Settings()
    settings.save_context_limit(10)
    assert settings.context_limit == 2_000
    settings.save_context_limit(10_000_000)
    assert settings.context_limit == 1_000_000


def test_warmup_defaults_on_and_roundtrips(config_path: Path) -> None:
    assert Settings().agent_warmup is True

    settings = Settings(agent_warmup=False)
    settings.save_config()
    assert Settings.load().agent_warmup is False


def test_warmup_can_be_disabled_by_env(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMMA_WARMUP", "0")
    assert Settings.load().agent_warmup is False

    monkeypatch.setenv("GEMMA_WARMUP", "yes")
    assert Settings.load().agent_warmup is True
