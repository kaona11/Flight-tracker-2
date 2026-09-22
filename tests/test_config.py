
from qsuite.config import Config
from qsuite.models import Cabin

YAML = """
scan:
  route: YYZ-DOH
  cabin: first
  days: 90
  providers: [alaska, ba]
runtime:
  global_concurrency: 4
  headless: false
storage:
  db_path: /tmp/x.sqlite3
credentials:
  qantas_token: "env:MY_QF_TOKEN"
providers:
  ba:
    coverage_verified: true
alerts:
  console: false
  qsuite_only: true
  min_seats: 2
"""


def test_yaml_is_read_into_the_config(tmp_path):
    p = tmp_path / "c.yml"
    p.write_text(YAML)
    cfg = Config.load(p)
    assert str(cfg.route) == "YYZ-DOH"
    assert cfg.cabin is Cabin.FIRST
    assert cfg.days == 90
    assert cfg.providers == ["alaska", "ba"]
    assert cfg.global_concurrency == 4
    assert cfg.headless is False
    assert cfg.alerts.qsuite_only is True
    assert cfg.alerts.min_seats == 2


def test_env_indirection_keeps_secrets_out_of_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_QF_TOKEN", "s3cret")
    p = tmp_path / "c.yml"
    p.write_text(YAML)
    cfg = Config.load(p)
    assert cfg.credentials["qantas_token"] == "s3cret"


def test_env_var_overrides_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("QSUITE_QANTAS_TOKEN", "from-env")
    p = tmp_path / "c.yml"
    p.write_text(YAML)
    assert Config.load(p).credentials["qantas_token"] == "from-env"


def test_provider_options_inherit_runtime_settings(tmp_path):
    p = tmp_path / "c.yml"
    p.write_text(YAML)
    cfg = Config.load(p)
    opts = cfg.options_for("ba")
    assert opts["coverage_verified"] is True
    assert opts["headless"] is False


def test_missing_config_file_is_an_error():
    import pytest
    with pytest.raises(FileNotFoundError):
        Config.load("definitely/not/here.yml")


def test_defaults_are_the_documented_ones():
    cfg = Config()
    assert str(cfg.route) == "YUL-SIN"
    assert cfg.cabin is Cabin.BUSINESS
    assert cfg.days == 330
    assert "qatar" not in cfg.providers, "Qatar must be opt-in, not a default"
