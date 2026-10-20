"""Step 3: speech-to-text with word timestamps (local, free).

Backends: faster-whisper (any Mac, CPU), mlx-whisper (Apple Silicon, fast), none.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from clipfactory.config import StreamerConfig, TranscribeConfig

log = logging.getLogger(__name__)

MLX_ALIASES = {
    "turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
}


class TranscribeError(RuntimeError):
    pass


class Word(BaseModel):
    start: float
    end: float
    text: str
    prob: float = 1.0


class Segment(BaseModel):
    start: float
    end: float
    text: str
    avg_logprob: float = 0.0
    words: list[Word] = Field(default_factory=list)


class Transcript(BaseModel):
    language: str = ""
    backend: str = ""
    model: str = ""
    segments: list[Segment] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments).strip()

    def words(self) -> list[Word]:
        return [w for s in self.segments for w in s.words]

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=1), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path) -> Transcript:
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))


class Transcriber(Protocol):
    name: str
    model: str

    def transcribe(self, path: Path, language: str | None) -> Transcript: ...


class NullTranscriber:
    name = "none"
    model = ""

    def transcribe(self, path: Path, language: str | None) -> Transcript:
        return Transcript(language=language or "", backend=self.name)


class FasterWhisperTranscriber:
    name = "faster-whisper"

    def __init__(self, model: str = "small", device: str = "auto", compute_type: str = "int8"):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscribeError(
                "faster-whisper is not installed: pip install -e '.[asr]'"
            ) from exc
        self.model = model
        self._model = WhisperModel(model, device=device, compute_type=compute_type)

    def transcribe(self, path: Path, language: str | None) -> Transcript:
        segments, info = self._model.transcribe(
            str(path), language=language, word_timestamps=True, vad_filter=True, beam_size=5
        )
        out: list[Segment] = []
        for seg in segments:
            words = [
                Word(start=w.start, end=w.end, text=w.word.strip(), prob=w.probability)
                for w in (seg.words or [])
            ]
            out.append(
                Segment(
                    start=seg.start,
                    end=seg.end,
                    text=seg.text.strip(),
                    avg_logprob=seg.avg_logprob,
                    words=words,
                )
            )
        return Transcript(
            language=getattr(info, "language", None) or language or "",
            backend=self.name,
            model=self.model,
            segments=out,
        )


class MlxWhisperTranscriber:
    name = "mlx"

    def __init__(self, model: str = "turbo"):
        try:
            import mlx_whisper
        except ImportError as exc:
            raise TranscribeError("mlx-whisper is not installed: pip install -e '.[mlx]'") from exc
        repo = MLX_ALIASES.get(model, model)
        if "/" not in repo:
            raise TranscribeError(
                "for the mlx backend set model to 'turbo' or an mlx-community/... repo id"
            )
        self.model = repo
        self._mlx = mlx_whisper

    def transcribe(self, path: Path, language: str | None) -> Transcript:
        result: dict[str, Any] = self._mlx.transcribe(
            str(path), path_or_hf_repo=self.model, word_timestamps=True, language=language
        )
        out: list[Segment] = []
        for seg in result.get("segments", []):
            words = [
                Word(
                    start=float(w["start"]),
                    end=float(w["end"]),
                    text=str(w["word"]).strip(),
                    prob=float(w.get("probability", 1.0)),
                )
                for w in seg.get("words", [])
            ]
            out.append(
                Segment(
                    start=float(seg["start"]),
                    end=float(seg["end"]),
                    text=str(seg["text"]).strip(),
                    avg_logprob=float(seg.get("avg_logprob", 0.0)),
                    words=words,
                )
            )
        return Transcript(
            language=str(result.get("language") or language or ""),
            backend=self.name,
            model=self.model,
            segments=out,
        )


def make_transcriber(cfg: TranscribeConfig) -> Transcriber:
    if cfg.backend == "none":
        return NullTranscriber()
    if cfg.backend == "mlx":
        return MlxWhisperTranscriber(cfg.model)
    return FasterWhisperTranscriber(cfg.model)


class LazyTranscriber:
    """Loads the model on first use, so runs with nothing to transcribe stay fast."""

    def __init__(self, cfg: TranscribeConfig) -> None:
        self._cfg = cfg
        self._inner: Transcriber | None = None
        self.name = cfg.backend
        self.model = cfg.model

    def transcribe(self, path: Path, language: str | None) -> Transcript:
        if self._inner is None:
            log.info("loading %s model %s", self._cfg.backend, self._cfg.model)
            self._inner = make_transcriber(self._cfg)
            self.model = self._inner.model
        return self._inner.transcribe(path, language)


def language_for(cfg: TranscribeConfig, streamer: StreamerConfig | None) -> str | None:
    lang = (streamer.language if streamer and streamer.language else None) or cfg.language
    return None if lang in ("", "auto") else lang
