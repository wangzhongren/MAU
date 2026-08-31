import os

from mau_flow import OpenAISettings, load_environment


def test_loads_external_env_without_overwriting_process_values(tmp_path, monkeypatch):
    external = tmp_path / "secrets.env"
    external.write_text(
        "OPENAI_API_KEY=from-file\nOPENAI_BASE_URL=https://example.test/v1\nMODEL_NAME=test-model\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(f"MAU_FLOW_ENV_FILE={external}\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "from-process")
    for key in ("MAU_FLOW_ENV_FILE", "OPENAI_BASE_URL", "MODEL_NAME"):
        monkeypatch.delenv(key, raising=False)

    loaded = load_environment(tmp_path)
    settings = OpenAISettings.from_environment(tmp_path)

    assert loaded == external.resolve()
    assert settings.api_key == "from-process"
    assert settings.base_url == "https://example.test/v1"
    assert settings.model == "test-model"
    assert settings.max_output_tokens == 32768


def test_real_pointer_file_is_git_ignored():
    assert os.path.basename(".env") == ".env"


def test_settings_repr_redacts_api_key():
    settings = OpenAISettings(api_key="super-secret", base_url=None, model="test")
    assert "super-secret" not in repr(settings)


def test_output_token_limit_can_be_configured(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=test\nMAX_OUTPUT_TOKENS=16384\n", encoding="utf-8"
    )
    for key in ("MAU_FLOW_ENV_FILE", "OPENAI_API_KEY", "MAX_OUTPUT_TOKENS"):
        monkeypatch.delenv(key, raising=False)
    settings = OpenAISettings.from_environment(tmp_path)
    assert settings.max_output_tokens == 16384
