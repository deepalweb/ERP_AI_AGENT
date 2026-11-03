from mcp_server.tools import model_config


def test_model_name_uses_muse_glimmer_as_default(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    assert model_config.model_name() == "meta/muse-glimmer-30b"


def test_model_name_prefers_openai_model_environment_variable(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "test/custom-model")

    assert model_config.model_name() == "test/custom-model"
