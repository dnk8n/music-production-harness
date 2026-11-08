from __future__ import annotations

from pathlib import Path

import pytest

from mph import config


def write(path: Path, text: str) -> Path:
    path.write_text(text, "utf-8")
    return path


@pytest.mark.req("REQ-CFG-01")
def test_defaults_load_without_user_file() -> None:
    cfg = config.load()
    assert cfg.audio.delivery_sample_rate == 48000
    assert cfg.audio.delivery_bit_depth == 24
    assert cfg.loaded_from == ()


@pytest.mark.req("REQ-CFG-01")
def test_user_file_overrides_defaults_and_merges_tables(tmp_path: Path) -> None:
    user = write(tmp_path / "c.toml", "[master]\ntarget_lufs_integrated = -9.0\n")
    cfg = config.load(user)
    assert cfg.master.target_lufs_integrated == -9.0
    assert cfg.master.max_true_peak_dbtp == -1.0  # untouched sibling key survives
    assert cfg.loaded_from == (user,)


@pytest.mark.req("REQ-CFG-01")
def test_env_var_selects_user_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user = write(tmp_path / "env.toml", "[llm]\nollama_model = 'x'\n")
    monkeypatch.setenv(config.ENV_CONFIG, str(user))
    assert config.load().ollama_model == "x"


@pytest.mark.req("REQ-CFG-01")
def test_explicit_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(config.ConfigError, match="not found"):
        config.load(tmp_path / "nope.toml")


@pytest.mark.req("REQ-CFG-01")
def test_arrays_replace_rather_than_merge() -> None:
    merged = config.deep_merge({"a": [1, 2], "t": {"x": 1}}, {"a": [3], "t": {"y": 2}})
    assert merged == {"a": [3], "t": {"x": 1, "y": 2}}


@pytest.mark.req("REQ-CFG-02")
def test_all_problems_reported_together(tmp_path: Path) -> None:
    user = write(
        tmp_path / "c.toml",
        """
[audio]
production_sample_rate = 12345
delivery_bit_depth = 32
delivery_format = "mp3"
""",
    )
    with pytest.raises(config.ConfigError) as err:
        config.load(user)
    assert len(err.value.problems) == 4  # rate, rate ordering, depth, format


@pytest.mark.req("REQ-CFG-02")
def test_unknown_key_is_reported(tmp_path: Path) -> None:
    user = write(tmp_path / "c.toml", "[master]\nceiling = 0\n")
    with pytest.raises(config.ConfigError, match=r"\[master\]"):
        config.load(user)


@pytest.mark.req("REQ-CFG-03")
def test_sources_parsed(tmp_path: Path) -> None:
    samples = tmp_path / "samples"
    user = write(
        tmp_path / "c.toml",
        f"""
[paths]
data_dir = "{tmp_path / "data"}"
[[sources]]
id = "dj"
kind = "local"
path = "{samples}"
license = "commercial-pack"
[[sources]]
id = "ia"
kind = "remote"
url = "https://archive.org"
""",
    )
    cfg = config.load(user)
    assert [s.id for s in cfg.sources] == ["dj", "ia"]
    assert cfg.sources[0].local_path == samples
    assert cfg.sources[1].local_path is None
    assert cfg.sources[1].license == "unknown"


@pytest.mark.req("REQ-CFG-03")
@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            'id = "a"\nkind = "local"\npath = "/x"\n'
            '[[sources]]\nid = "a"\nkind = "local"\npath = "/y"',
            "duplicate source id",
        ),
        ('id = "a"\nkind = "ftp"\npath = "/x"', "kind must be"),
        ('id = "a"\nkind = "local"', "needs a path"),
        ('kind = "local"', "missing"),
    ],
)
def test_bad_sources_rejected(tmp_path: Path, body: str, message: str) -> None:
    user = write(tmp_path / "c.toml", f'[paths]\ndata_dir = "{tmp_path}/d"\n[[sources]]\n{body}\n')
    with pytest.raises(config.ConfigError, match=message):
        config.load(user)


@pytest.mark.req("REQ-CFG-04")
@pytest.mark.parametrize("data_rel", ["samples", "samples/cache", "."])
def test_data_dir_may_not_overlap_source(tmp_path: Path, data_rel: str) -> None:
    user = write(
        tmp_path / "c.toml",
        f"""
[paths]
data_dir = "{tmp_path / data_rel}"
[[sources]]
id = "dj"
kind = "local"
path = "{tmp_path / "samples"}"
""",
    )
    with pytest.raises(config.ConfigError, match="overlaps read-only source"):
        config.load(user)


@pytest.mark.req("REQ-CFG-05")
def test_production_rate_below_delivery_rejected(tmp_path: Path) -> None:
    user = write(tmp_path / "c.toml", "[audio]\nproduction_sample_rate = 44100\n")
    with pytest.raises(config.ConfigError, match=">= delivery_sample_rate"):
        config.load(user)


@pytest.mark.req("REQ-CFG-03")
def test_example_config_is_valid() -> None:
    example = Path(__file__).resolve().parents[1] / "config" / "example.toml"
    cfg = config.load(example)
    assert cfg.sources[0].id == "dj-samples"
