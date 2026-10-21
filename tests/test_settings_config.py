from pathlib import Path

import pytest
from pydantic import ValidationError

from clipfactory.config import AppConfig, ConfigError, NormBox, StreamerConfig, load_config
from clipfactory.settings import Settings, SettingsError, parse_dotenv

REPO = Path(__file__).resolve().parents[1]


def test_parse_dotenv_handles_comments_quotes_export():
    text = """
    # comment
    TWITCH_CLIENT_ID=abc123
    export TELEGRAM_CHAT_ID=42
    TWITCH_CLIENT_SECRET="se cret"
    FFMPEG_BIN=/opt/bin/ffmpeg  # inline comment
    BROKEN_LINE
    """
    values = parse_dotenv(text)
    assert values == {
        "TWITCH_CLIENT_ID": "abc123",
        "TELEGRAM_CHAT_ID": "42",
        "TWITCH_CLIENT_SECRET": "se cret",
        "FFMPEG_BIN": "/opt/bin/ffmpeg",
    }


def test_settings_load_env_file_and_environment_wins(tmp_path):
    env = tmp_path / ".env"
    env.write_text("TWITCH_CLIENT_ID=from_file\nTELEGRAM_CHAT_ID=123\n", encoding="utf-8")
    s = Settings.load(env, environ={"TWITCH_CLIENT_ID": "from_env"})
    assert s.twitch_client_id == "from_env"
    assert s.telegram_chat_id == 123
    assert s.db_path == Path("data/clipfactory.sqlite3")


def test_settings_rejects_non_numeric_chat_id(tmp_path):
    with pytest.raises(SettingsError):
        Settings.load(tmp_path / "missing.env", environ={"TELEGRAM_CHAT_ID": "abc"})


def test_example_config_is_valid():
    cfg = load_config(REPO / "config" / "streamers.example.yaml")
    streamer = cfg.streamer("EXAMPLE_STREAMER")
    assert streamer is not None and streamer.can_fetch
    assert streamer.resolved_layout == "split"


def test_missing_config_has_helpful_error(tmp_path):
    with pytest.raises(ConfigError, match="clipfactory init"):
        load_config(tmp_path / "nope.yaml")


def test_invalid_config_lists_problems(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("streamers:\n  - login: 'bad login!'\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not a Twitch login"):
        load_config(path)


def test_split_layout_requires_webcam():
    with pytest.raises(ValidationError, match="webcam"):
        StreamerConfig(login="abc", layout="split")


def test_listed_streamer_can_be_fetched_unless_paused():
    assert StreamerConfig(login="abc").can_fetch
    assert not StreamerConfig(login="abc", permission="unknown").can_fetch
    assert not StreamerConfig(login="abc", permission="denied").can_fetch


def test_duplicate_logins_rejected():
    with pytest.raises(ValidationError, match="twice"):
        AppConfig(streamers=[StreamerConfig(login="abc"), StreamerConfig(login="ABC")])


def test_normbox_parse_and_bounds():
    box = NormBox.parse("0.72, 0.70, 0.28, 0.30")
    assert (box.x, box.w) == (0.72, 0.28)
    with pytest.raises(ValidationError):
        NormBox(x=0.8, y=0.0, w=0.3, h=0.2)
    with pytest.raises(ValueError):
        NormBox.parse("1,2,3")
