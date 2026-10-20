"""config/streamers.yaml: the streamer whitelist, permissions, layout and pipeline knobs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

Permission = Literal["explicit", "unknown", "denied"]
LayoutName = Literal["split", "full"]

LOGIN_RE = re.compile(r"^[a-z0-9_]{2,25}$")


class ConfigError(ValueError):
    pass


class NormBox(BaseModel):
    """A box in fractions of the source frame (0..1), resolution independent."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def _inside_frame(self) -> NormBox:
        if self.x + self.w > 1.0001 or self.y + self.h > 1.0001:
            raise ValueError("box must fit inside the frame (x + w <= 1 and y + h <= 1)")
        return self

    @classmethod
    def parse(cls, text: str) -> NormBox:
        parts = [p.strip() for p in text.split(",")]
        if len(parts) != 4:
            raise ValueError("expected x,y,w,h, e.g. 0.72,0.70,0.28,0.30")
        x, y, w, h = (float(p) for p in parts)
        return cls(x=x, y=y, w=w, h=h)

    def as_text(self) -> str:
        return f"{self.x:g},{self.y:g},{self.w:g},{self.h:g}"


class StreamerConfig(BaseModel):
    login: str
    display_name: str | None = None
    permission: Permission = "unknown"
    permission_proof: str = ""
    permission_note: str = ""
    layout: Literal["auto", "split", "full"] = "auto"
    webcam: NormBox | None = None
    game_center_x: float = Field(0.5, ge=0, le=1)
    hook_speaker: str | None = None
    language: str | None = None
    weight: float = Field(1.0, gt=0)

    @field_validator("login")
    @classmethod
    def _normalize_login(cls, value: str) -> str:
        login = value.strip().lower()
        if not LOGIN_RE.match(login):
            raise ValueError(f"not a Twitch login: {value!r}")
        return login

    @model_validator(mode="after")
    def _split_needs_webcam(self) -> StreamerConfig:
        if self.layout == "split" and self.webcam is None:
            raise ValueError(
                f"{self.login}: layout 'split' needs a webcam box (see `clipfactory calibrate`)"
            )
        return self

    @property
    def can_fetch(self) -> bool:
        """Media may be downloaded only with explicit permission and a recorded proof."""
        return self.permission == "explicit" and bool(self.permission_proof.strip())

    @property
    def resolved_layout(self) -> LayoutName:
        if self.layout == "full":
            return "full"
        return "split" if self.webcam is not None else "full"

    @property
    def speaker(self) -> str:
        return self.hook_speaker or self.display_name or self.login


class DiscoveryConfig(BaseModel):
    lookback_hours: int = Field(48, ge=1, le=24 * 30)
    min_views: int = Field(30, ge=0)
    game_name: str = "Counter-Strike"
    game_only: bool = True
    max_pages: int = Field(3, ge=1, le=20)


class PipelineConfig(BaseModel):
    clips_per_run: int = Field(10, ge=1, le=100)


class TranscribeConfig(BaseModel):
    backend: Literal["faster-whisper", "mlx", "none"] = "faster-whisper"
    model: str = "small"
    language: str = "ru"


class RenderConfig(BaseModel):
    max_duration: float = Field(45.0, ge=5, le=180)
    hook_seconds: float = Field(6.0, ge=0)
    hook_color: str = "#FFE600"
    captions: bool = False
    credit: bool = True
    font: str = "assets/fonts/InterDisplay-Black.otf"
    encoder: Literal["libx264", "h264_videotoolbox"] = "libx264"
    crf: int = Field(20, ge=10, le=35)
    max_bitrate: str = "7M"
    loudness_lufs: float = Field(-14.0, ge=-30, le=-8)
    fit_zoom: float = Field(1.0, ge=1.0, le=2.0)

    @field_validator("hook_color")
    @classmethod
    def _hex_color(cls, value: str) -> str:
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            raise ValueError("hook_color must look like #FFE600")
        return value

    @field_validator("max_bitrate")
    @classmethod
    def _bitrate(cls, value: str) -> str:
        if not re.fullmatch(r"\d+(\.\d+)?[kKmM]?", value):
            raise ValueError("max_bitrate must look like 7M or 6500k")
        return value


class AppConfig(BaseModel):
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    transcribe: TranscribeConfig = Field(default_factory=TranscribeConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    streamers: list[StreamerConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_logins(self) -> AppConfig:
        seen: set[str] = set()
        for s in self.streamers:
            if s.login in seen:
                raise ValueError(f"streamer listed twice: {s.login}")
            seen.add(s.login)
        return self

    def streamer(self, login: str) -> StreamerConfig | None:
        login = login.lower()
        return next((s for s in self.streamers if s.login == login), None)


def load_config(path: str | Path) -> AppConfig:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(
            f"{path} not found. Run `clipfactory init` or copy config/streamers.example.yaml."
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        lines = []
        for err in exc.errors():
            where = ".".join(str(p) for p in err["loc"]) or "(root)"
            lines.append(f"  {where}: {err['msg']}")
        raise ConfigError(f"{path} is invalid:\n" + "\n".join(lines)) from exc
