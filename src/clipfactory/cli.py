"""Command line entry point: `clipfactory --help`."""

from __future__ import annotations

import logging
import shutil
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import click

from clipfactory import __version__, db, doctor
from clipfactory.config import AppConfig, ConfigError, NormBox, load_config
from clipfactory.models import ClipStatus, RenderStatus
from clipfactory.settings import Settings, SettingsError

log = logging.getLogger("clipfactory")


class State:
    def __init__(self, env_file: str) -> None:
        self.env_file = env_file
        self._settings: Settings | None = None
        self._config: AppConfig | None = None
        self._conn: sqlite3.Connection | None = None

    @property
    def settings(self) -> Settings:
        if self._settings is None:
            try:
                self._settings = Settings.load(self.env_file)
            except SettingsError as exc:
                raise click.ClickException(str(exc)) from exc
        return self._settings

    @property
    def config(self) -> AppConfig:
        if self._config is None:
            try:
                self._config = load_config(self.settings.config_path)
            except ConfigError as exc:
                raise click.ClickException(str(exc)) from exc
        return self._config

    def config_or_default(self) -> AppConfig:
        try:
            return self.config
        except click.ClickException:
            if self.settings.config_path.exists():
                raise
            return AppConfig()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.settings.ensure_dirs()
            self._conn = db.connect(self.settings.db_path)
        return self._conn


pass_state = click.make_pass_decorator(State)


def _fail(exc: Exception) -> click.ClickException:
    return click.ClickException(f"{type(exc).__name__}: {exc}")


@click.group()
@click.version_option(__version__, prog_name="clipfactory")
@click.option("--env-file", default=".env", show_default=True, help="File with secrets.")
@click.option("-v", "--verbose", is_flag=True, help="Debug logging.")
@click.pass_context
def cli(ctx: click.Context, env_file: str, verbose: bool) -> None:
    """CS2 Clip Factory: permitted Twitch clips -> reviewed 9:16 videos."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # httpx logs full request URLs at INFO, and Telegram URLs contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    ctx.obj = State(env_file)


# --- setup -------------------------------------------------------------------------------


@cli.command()
@pass_state
def init(state: State) -> None:
    """Create .env, config/streamers.yaml, data folders and the database."""
    pairs = (
        (Path(".env.example"), Path(state.env_file)),
        (Path("config/streamers.example.yaml"), state.settings.config_path),
    )
    for src, dst in pairs:
        if dst.exists():
            click.echo(f"exists   {dst}")
        elif src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            click.echo(f"created  {dst}")
        else:
            click.echo(f"missing  {src} (run from the repository root)", err=True)
    Path("permissions").mkdir(exist_ok=True)
    state.conn  # noqa: B018 - creates folders and the database
    click.echo(f"database {state.settings.db_path}")
    click.echo("Next: fill in .env, edit config/streamers.yaml, then run `clipfactory doctor`.")


@cli.command("doctor")
@pass_state
def doctor_command(state: State) -> None:
    """Check tools, secrets and config before the first run."""
    settings = state.settings
    checks: list[doctor.Check] = []
    try:
        cfg = state.config
        config_ok = True
    except click.ClickException as exc:
        cfg, config_ok = AppConfig(), False
        checks.append(doctor.Check("config", "fail", exc.message))
    checks += doctor.check_tools(settings, cfg)
    checks += doctor.check_assets(cfg)
    checks += doctor.check_asr(cfg)
    checks += doctor.check_secrets(settings)
    if config_ok:
        checks += doctor.check_streamers(cfg)

    marks = {"ok": click.style("ok  ", fg="green"), "warn": click.style("warn", fg="yellow"),
             "fail": click.style("FAIL", fg="red")}  # fmt: skip
    for check in checks:
        click.echo(f"{marks[check.status]}  {check.name}: {check.detail}")
    failed = sum(c.status == "fail" for c in checks)
    if failed:
        raise click.ClickException(f"{failed} check(s) failed")
    click.echo("All required checks passed.")


# --- pipeline steps ----------------------------------------------------------------------


def _helix(settings: Settings):
    from clipfactory.twitch import HelixClient, TwitchError

    try:
        return HelixClient(settings.twitch_client_id, settings.twitch_client_secret)
    except TwitchError as exc:
        raise _fail(exc) from exc


@cli.command("discover")
@pass_state
def discover_command(state: State) -> None:
    """Find fresh clips for whitelisted streamers (metadata only)."""
    from clipfactory.discover import discover
    from clipfactory.twitch import TwitchError

    with _helix(state.settings) as helix:
        try:
            report = discover(state.conn, helix, state.config)
        except TwitchError as exc:
            raise _fail(exc) from exc
    click.echo(f"new: {report.new}, updated: {report.updated}, filtered: {report.filtered}")
    if report.missing_streamers:
        click.echo(f"not found on Twitch: {', '.join(report.missing_streamers)}", err=True)


def _rows(state: State, clip_id: str | None, statuses: list[ClipStatus], limit: int):
    if clip_id:
        row = db.get_clip(state.conn, clip_id)
        if row is None:
            raise click.ClickException(f"clip not found: {clip_id}")
        return [row]
    return db.list_clips(state.conn, statuses=statuses, limit=limit)


@cli.command("fetch")
@click.option("--limit", type=int, default=None, help="How many top clips (default from config).")
@click.option("--clip", "clip_id", default=None, help="Fetch one clip by id.")
@pass_state
def fetch_command(state: State, limit: int | None, clip_id: str | None) -> None:
    """Download top clips of the streamers listed in config/streamers.yaml."""
    from clipfactory.fetch import fetch_clip, ytdlp_downloader
    from clipfactory.pipeline import select_candidates

    cfg, settings = state.config, state.settings
    rows = (
        _rows(state, clip_id, [], 1)
        if clip_id
        else select_candidates(state.conn, cfg, limit or cfg.pipeline.clips_per_run)
    )
    downloader = ytdlp_downloader(settings.ytdlp_command())
    for row in rows:
        outcome = fetch_clip(state.conn, settings, cfg, row, downloader)
        click.echo(f"{outcome.status:<9} {row['clip_id']} {outcome.reason}")


@cli.command("transcribe")
@click.option("--limit", type=int, default=20)
@click.option("--clip", "clip_id", default=None)
@pass_state
def transcribe_command(state: State, limit: int, clip_id: str | None) -> None:
    """Speech-to-text for fetched clips."""
    from clipfactory.pipeline import transcribe_clip
    from clipfactory.transcribe import LazyTranscriber

    transcriber = LazyTranscriber(state.config.transcribe)
    for row in _rows(state, clip_id, [ClipStatus.FETCHED], limit):
        try:
            transcript = transcribe_clip(state.conn, state.settings, state.config, row, transcriber)
        except Exception as exc:  # noqa: BLE001 - report and continue with the next clip
            click.echo(f"failed    {row['clip_id']} {exc}", err=True)
            continue
        click.echo(f"ok        {row['clip_id']} {transcript.text[:80]!r}")


@cli.command("render")
@click.option("--clip", "clip_id", default=None, help="Render one clip by id.")
@click.option("--hook", default=None, help="Use this hook instead of the suggestions.")
@click.option("--start", type=float, default=None, help="Trim start, seconds.")
@click.option("--end", type=float, default=None, help="Trim end, seconds.")
@click.option("--limit", type=int, default=20)
@pass_state
def render_command(
    state: State,
    clip_id: str | None,
    hook: str | None,
    start: float | None,
    end: float | None,
    limit: int,
) -> None:
    """Render fetched/transcribed clips into review drafts."""
    from clipfactory.pipeline import render_clip

    rows = _rows(state, clip_id, [ClipStatus.FETCHED, ClipStatus.TRANSCRIBED], limit)
    for row in rows:
        t0 = time.monotonic()
        try:
            render_id = render_clip(
                state.conn, state.settings, state.config, row, hook_text=hook, start=start, end=end
            )
        except Exception as exc:  # noqa: BLE001 - report and continue with the next clip
            click.echo(f"failed    {row['clip_id']} {exc}", err=True)
            continue
        render = db.get_render(state.conn, render_id)
        click.echo(
            f"#{render_id:<4} {render['path']} ({time.monotonic() - t0:.1f}s) "
            f"hook: {render['hook_text']!r}"
        )


def _bot(state: State):
    from clipfactory.pipeline import export_render, rerender
    from clipfactory.review import ReviewBot
    from clipfactory.telegram import TelegramClient, TelegramError

    settings = state.settings
    if settings.telegram_chat_id is None:
        raise click.ClickException("TELEGRAM_CHAT_ID is not set: run `clipfactory review chat-id`")
    try:
        tg = TelegramClient(settings.telegram_bot_token)
    except TelegramError as exc:
        raise _fail(exc) from exc
    conn, cfg = state.conn, state.config
    from clipfactory.run_control import start_pipeline

    return ReviewBot(
        conn,
        settings,
        tg,
        rerender=lambda render_id, hook: rerender(conn, settings, cfg, render_id, hook),
        export=lambda render_id: export_render(conn, settings, render_id),
        start_pipeline=lambda: start_pipeline(settings.data_dir, Path.cwd(), limit=1),
    )


@cli.command("run")
@click.option("--limit", type=int, default=None, help="Clips to process (default from config).")
@click.option("--no-discover", is_flag=True, help="Skip the Twitch discovery step.")
@click.option("--no-review", is_flag=True, help="Don't send drafts to Telegram.")
@pass_state
def run_command(state: State, limit: int | None, no_discover: bool, no_review: bool) -> None:
    """Discover, render and send clips; only one pipeline run can be active."""
    from clipfactory.run_control import PipelineBusy, pipeline_lock

    try:
        with pipeline_lock(state.settings.data_dir):
            _run_once(state, limit, no_discover, no_review)
    except PipelineBusy as exc:
        raise click.ClickException(str(exc)) from exc


def _run_once(state: State, limit: int | None, no_discover: bool, no_review: bool) -> None:
    """Execute a run while the caller holds the shared pipeline lock."""
    from clipfactory.fetch import ytdlp_downloader
    from clipfactory.pipeline import run_pipeline
    from clipfactory.transcribe import LazyTranscriber
    from clipfactory.twitch import TwitchError

    settings, cfg = state.settings, state.config
    source = None if no_discover else _helix(settings)
    try:
        report = run_pipeline(
            state.conn,
            settings,
            cfg,
            downloader=ytdlp_downloader(settings.ytdlp_command()),
            transcriber=LazyTranscriber(cfg.transcribe),
            source=source,
            limit=limit,
        )
    except TwitchError as exc:
        raise _fail(exc) from exc
    finally:
        if source is not None:
            source.close()
    if report.discovered:
        d = report.discovered
        click.echo(f"discovered: new {d.new}, updated {d.updated}, filtered {d.filtered}")
    click.echo(
        f"selected {report.selected}, rendered {len(report.rendered)}, "
        f"not rendered {len(report.not_rendered)}"
    )
    if not no_review and report.rendered and settings.telegram_bot_token:
        sent = _bot(state).send_pending()
        click.echo(f"sent to Telegram for review: {sent}")


@cli.group()
def review() -> None:
    """Telegram review queue."""


@review.command("send")
@pass_state
def review_send(state: State) -> None:
    """Send all draft renders to Telegram."""
    click.echo(f"sent: {_bot(state).send_pending()}")


@review.command("run")
@pass_state
def review_run(state: State) -> None:
    """Run the bot (buttons: approve, reject, other hook, own hook). Ctrl+C to stop."""
    bot = _bot(state)
    click.echo("Bot is running. Send /pending in Telegram. Ctrl+C to stop.")
    try:
        bot.run()
    except KeyboardInterrupt:
        click.echo("stopped")


@review.command("chat-id")
@pass_state
def review_chat_id(state: State) -> None:
    """Print chat ids that messaged the bot (send it any message first)."""
    from clipfactory.review import discover_chat_ids
    from clipfactory.telegram import TelegramClient, TelegramError

    try:
        chats = discover_chat_ids(TelegramClient(state.settings.telegram_bot_token))
    except TelegramError as exc:
        raise _fail(exc) from exc
    if not chats:
        click.echo("No messages yet: open your bot in Telegram, press Start, then run this again.")
    for chat_id, name in chats:
        click.echo(f"TELEGRAM_CHAT_ID={chat_id}   # {name}")


# --- inspection and tools ----------------------------------------------------------------


@cli.command()
@click.option("--top", type=int, default=10, help="Show the top N discovered clips.")
@pass_state
def status(state: State, top: int) -> None:
    """Counts per state and the best clips waiting."""
    counts = db.status_counts(state.conn)
    click.echo("clips:   " + (", ".join(f"{k} {v}" for k, v in counts["clips"].items()) or "none"))
    click.echo(
        "renders: " + (", ".join(f"{k} {v}" for k, v in counts["renders"].items()) or "none")
    )
    rows = db.list_clips(state.conn, statuses=[ClipStatus.DISCOVERED], limit=top)
    if rows:
        click.echo("\ntop discovered clips:")
    for row in rows:
        click.echo(
            f"  {row['score']:>9.2f}  {row['view_count']:>7} views  {row['broadcaster_login']:<16} "
            f"{row['title'][:50]}"
        )


@cli.command()
@click.argument("login")
@click.option("--file", "file_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--clip", "clip_id", default=None, help="Use a fetched clip by id.")
@click.option("--at", type=float, default=None, help="Second to grab (default: middle).")
@click.option("--box", default=None, help="Draw this webcam box (x,y,w,h) to check it.")
@pass_state
def calibrate(
    state: State,
    login: str,
    file_path: Path | None,
    clip_id: str | None,
    at: float | None,
    box: str | None,
) -> None:
    """Grab a frame with a 10% grid (and a suggested webcam box) for a streamer."""
    from clipfactory.calibrate import calibrate_frame
    from clipfactory.media import extract_frame, probe
    from clipfactory.render import resolve_font

    settings = state.settings
    cfg = state.config_or_default()
    if file_path is None:
        m = (
            db.get_media(state.conn, clip_id)
            if clip_id
            else db.latest_media_for_login(state.conn, login)
        )
        if m is None:
            raise click.ClickException("no fetched clip for this streamer: pass --file clip.mp4")
        file_path = Path(m["path"])
    info = probe(file_path, settings.ffprobe_bin)
    out_dir = settings.work_dir / "calibrate"
    frame = extract_frame(
        file_path,
        at if at is not None else info.duration / 2,
        out_dir / f"{login}_frame.png",
        settings.ffmpeg_bin,
    )
    streamer = cfg.streamer(login)
    try:
        chosen = NormBox.parse(box) if box else (streamer.webcam if streamer else None)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--box") from exc
    result = calibrate_frame(
        frame, out_dir / f"{login}_grid.png", resolve_font(cfg.render.font), box=chosen
    )
    click.echo(f"open: {result.image}")
    if result.suggestion:
        s = result.suggestion
        click.echo(f"suggested:  webcam: {{x: {s.x}, y: {s.y}, w: {s.w}, h: {s.h}}}")
    else:
        click.echo("no face found automatically; read the box off the grid")
    click.echo(f"check it:   clipfactory render-file {file_path} --webcam X,Y,W,H --end 6")


@cli.command("render-file")
@click.argument("src", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path), default=None)
@click.option(
    "--webcam", default=None, help="Webcam box x,y,w,h (fractions). Omit for full layout."
)
@click.option("--auto-webcam", is_flag=True, help="Detect a webcam across five frames.")
@click.option("--hook", default="", help='On-screen line, e.g. "Electronic: я бы замашнил".')
@click.option("--credit", default="", help='Credit line, e.g. "twitch.tv/streamer".')
@click.option("--start", type=float, default=None)
@click.option("--end", type=float, default=None)
@click.option("--center-x", type=float, default=0.5, help="Horizontal center of the game crop.")
@pass_state
def render_file(
    state: State,
    src: Path,
    out: Path | None,
    webcam: str | None,
    auto_webcam: bool,
    hook: str,
    credit: str,
    start: float | None,
    end: float | None,
    center_x: float,
) -> None:
    """Render any local video file — no Twitch, no database. Good for testing layouts."""
    from clipfactory.render import RenderError, render_video

    settings = state.settings
    cfg = state.config_or_default()
    try:
        box = NormBox.parse(webcam) if webcam else None
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--webcam") from exc
    out = out or settings.out_dir / "test" / f"{src.stem}_9x16.mp4"
    t0 = time.monotonic()
    try:
        result = render_video(
            src,
            out,
            cfg=cfg.render.model_copy(update={"auto_webcam": True}) if auto_webcam else cfg.render,
            layout="split" if box or auto_webcam else "full",
            webcam=box,
            game_center_x=center_x,
            hook_text=hook,
            credit_text=credit,
            start=start,
            end=end,
            ffmpeg_bin=settings.ffmpeg_bin,
            ffprobe_bin=settings.ffprobe_bin,
            work_dir=settings.work_dir,
        )
    except RenderError as exc:
        raise _fail(exc) from exc
    click.echo(
        f"{result.path}  ({result.layout}, {result.length:.1f}s, "
        f"rendered in {time.monotonic() - t0:.1f}s)"
    )


@cli.command()
@click.option("--days", type=int, default=14, show_default=True)
@click.option("--dry-run", is_flag=True)
@pass_state
def cleanup(state: State, days: int, dry_run: bool) -> None:
    """Delete old source downloads and discarded renders (out/ready is never touched)."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    victims: list[Path] = []
    for m in db.list_media(state.conn):
        if datetime.fromisoformat(m["fetched_at"]) < cutoff:
            victims.append(Path(m["path"]))
    discarded = [RenderStatus.REJECTED, RenderStatus.SUPERSEDED]
    for r in db.list_renders(state.conn, statuses=discarded, limit=100_000):
        if datetime.fromisoformat(r["created_at"]) < cutoff:
            victims.append(Path(r["path"]))
    freed = 0
    for path in victims:
        if path.is_file():
            freed += path.stat().st_size
            if not dry_run:
                path.unlink()
    verb = "would free" if dry_run else "freed"
    click.echo(f"{verb} {freed / 1e6:.1f} MB")
    if not dry_run:
        db.log_event(state.conn, "cleanup", detail=f"{days}d, {freed} bytes")


@cli.group()
def schedule() -> None:
    """Autostart on macOS: bot always on, pipeline on a daily schedule."""


def _scheduler():
    from clipfactory.schedule import Scheduler

    project = Path.cwd()
    if not (project / "pyproject.toml").is_file():
        raise click.ClickException("run this from the cs2-clip-factory folder")
    return Scheduler(project=project)


@schedule.command("install")
@click.option(
    "--at",
    "times",
    multiple=True,
    default=("12:00", "20:00"),
    show_default=True,
    help="Daily run time, HH:MM (repeat for several runs).",
)
@click.option("--limit", type=int, default=3, show_default=True, help="Clips per run.")
def schedule_install(times: tuple[str, ...], limit: int) -> None:
    """Install (or update) both background jobs."""
    from clipfactory.schedule import ScheduleError

    try:
        paths = _scheduler().install(times, limit)
    except ScheduleError as exc:
        raise _fail(exc) from exc
    for path in paths:
        click.echo(f"installed {path}")
    click.echo(f"Bot is running in the background; clips are collected at {', '.join(times)}.")
    click.echo("Logs: data/logs/bot.log and data/logs/run.log. Don't start `review run` by hand.")


@schedule.command("status")
def schedule_status() -> None:
    """Show whether the background jobs are loaded."""
    for label, state in _scheduler().status().items():
        click.echo(f"{label}: {state}")


@schedule.command("now")
def schedule_now() -> None:
    """Start the scheduled pipeline run right now (in the background)."""
    from clipfactory.schedule import ScheduleError

    try:
        _scheduler().run_now()
    except ScheduleError as exc:
        raise _fail(exc) from exc
    click.echo("Started. Follow it with: tail -f data/logs/run.log")


@schedule.command("uninstall")
def schedule_uninstall() -> None:
    """Stop and remove both background jobs."""
    removed = _scheduler().uninstall()
    click.echo("removed: " + (", ".join(str(p) for p in removed) or "nothing was installed"))


def main() -> None:
    cli(prog_name="clipfactory")
