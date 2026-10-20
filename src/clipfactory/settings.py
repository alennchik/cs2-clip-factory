"""Runtime settings: secrets and paths come from the environment or a local .env file.

Secrets never live in git or in config/streamers.yaml.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel


class SettingsError(ValueError):
    pass


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines. Supports comments, `export`, and quoted values."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key:
            values[key] = value
    return values


class Settings(BaseModel):
    twitch_client_id: str = ""
    twitch_client_secret: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: int | None = None
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    ytdlp_bin: str = ""  # empty -> run yt-dlp as a module of the current interpreter
    data_dir: Path = Path("data")
    out_dir: Path = Path("out")
    config_path: Path = Path("config/streamers.yaml")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "clipfactory.sqlite3"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def transcripts_dir(self) -> Path:
        return self.data_dir / "transcripts"

    @property
    def renders_dir(self) -> Path:
        return self.data_dir / "renders"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def ready_dir(self) -> Path:
        return self.out_dir / "ready"

    def ensure_dirs(self) -> None:
        for path in (
            self.raw_dir,
            self.transcripts_dir,
            self.renders_dir,
            self.work_dir,
            self.ready_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def ytdlp_command(self) -> list[str]:
        if self.ytdlp_bin:
            return [self.ytdlp_bin]
        return [sys.executable, "-m", "yt_dlp"]

    @classmethod
    def load(
        cls, env_file: str | Path = ".env", environ: Mapping[str, str] | None = None
    ) -> Settings:
        env = dict(os.environ if environ is None else environ)
        path = Path(env_file)
        if path.is_file():
            for key, value in parse_dotenv(path.read_text(encoding="utf-8")).items():
                env.setdefault(key, value)

        chat = env.get("TELEGRAM_CHAT_ID", "").strip()
        try:
            chat_id = int(chat) if chat else None
        except ValueError as exc:
            raise SettingsError(f"TELEGRAM_CHAT_ID must be a number, got {chat!r}") from exc

        return cls(
            twitch_client_id=env.get("TWITCH_CLIENT_ID", "").strip(),
            twitch_client_secret=env.get("TWITCH_CLIENT_SECRET", "").strip(),
            telegram_bot_token=env.get("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_chat_id=chat_id,
            ffmpeg_bin=env.get("FFMPEG_BIN") or "ffmpeg",
            ffprobe_bin=env.get("FFPROBE_BIN") or "ffprobe",
            ytdlp_bin=env.get("YTDLP_BIN", ""),
            data_dir=Path(env.get("CLIPFACTORY_DATA_DIR") or "data"),
            out_dir=Path(env.get("CLIPFACTORY_OUT_DIR") or "out"),
            config_path=Path(env.get("CLIPFACTORY_CONFIG") or "config/streamers.yaml"),
        )
