"""Telegram review queue: a human approves every video before it can be posted."""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from clipfactory import db
from clipfactory.models import RenderStatus
from clipfactory.render import TELEGRAM_UPLOAD_LIMIT, make_preview
from clipfactory.settings import Settings

log = logging.getLogger(__name__)

HELP = (
    "Сюда приходят ролики на проверку.\n"
    "✅ — одобрить: пришлю финальный файл и подпись для TikTok.\n"
    "❌ — отклонить.\n"
    "Хук 1/2/3 — перерисовать с другим вариантом хука.\n"
    "✏️ — прислать свой хук текстом (формат «Кто: фраза»).\n\n"
    "/pending — прислать готовые ролики, /status — статистика.\n"
    "/new — создать один новый ролик на Mac (если он включён)."
)
MAIN_MENU = {
    "keyboard": [
        [{"text": "🎬 Создать ролик"}],
        [{"text": "📋 Очередь"}, {"text": "📊 Статус"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}
ACTIONS = {"a", "r", "e", "h", "p"}
OPEN = (RenderStatus.DRAFT, RenderStatus.SENT)


class BotAPI(Protocol):
    def get_updates(self, offset: int, timeout: int = 50) -> list[dict[str, Any]]: ...

    def send_message(
        self, chat_id: int, text: str, reply_markup: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...

    def send_video(self, chat_id: int, path: Path, caption: str = "", reply_markup=None, **kw): ...

    def send_document(self, chat_id: int, path: Path, caption: str = "", reply_markup=None): ...

    def answer_callback(self, callback_id: str, text: str = "") -> None: ...

    def clear_buttons(self, chat_id: int, message_id: int) -> None: ...


def parse_callback(data: str) -> tuple[str, int, int | None]:
    parts = data.split(":")
    if len(parts) not in (2, 3) or parts[0] not in ACTIONS:
        raise ValueError(f"bad callback data: {data!r}")
    render_id = int(parts[1])
    extra = int(parts[2]) if len(parts) == 3 else None
    if parts[0] == "h" and extra is None:
        raise ValueError("hook option index missing")
    return parts[0], render_id, extra


def discover_chat_ids(tg: BotAPI, timeout: int = 20) -> list[tuple[int, str]]:
    """Chats that recently messaged the bot (without consuming the updates)."""
    seen: dict[int, str] = {}
    for update in tg.get_updates(0, timeout=timeout):
        message = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
        chat = message.get("chat") or {}
        if "id" in chat:
            seen[int(chat["id"])] = str(chat.get("username") or chat.get("first_name") or "")
    return list(seen.items())


@dataclass
class ReviewBot:
    conn: sqlite3.Connection
    settings: Settings
    tg: BotAPI
    rerender: Callable[[int, str], int]
    export: Callable[[int], Path]
    pending_edit: dict[int, int] = field(default_factory=dict)
    start_pipeline: Callable[[], int] | None = None

    @property
    def owner(self) -> int:
        if self.settings.telegram_chat_id is None:
            raise RuntimeError("TELEGRAM_CHAT_ID is not set (run `clipfactory review chat-id`)")
        return self.settings.telegram_chat_id

    # --- outgoing ----------------------------------------------------------------------

    def keyboard(self, render: sqlite3.Row) -> dict[str, Any]:
        rid = render["id"]
        rows: list[list[dict[str, str]]] = [
            [
                {"text": "✅ Публикую", "callback_data": f"a:{rid}"},
                {"text": "❌ Мимо", "callback_data": f"r:{rid}"},
            ]
        ]
        alternatives = [
            {"text": f"Хук {i + 1}", "callback_data": f"h:{rid}:{i}"}
            for i, option in enumerate(db.hook_options(render)[:3])
            if option != render["hook_text"]
        ]
        if alternatives:
            rows.append(alternatives)
        rows.append([{"text": "✏️ Свой хук", "callback_data": f"e:{rid}"}])
        return {"inline_keyboard": rows}

    def caption(self, render: sqlite3.Row, clip: sqlite3.Row) -> str:
        lines = []
        if db.latest_event_detail(self.conn, "webcam_detection", render["id"]) is not None:
            lines.append(
                "⚠️ Автовебка: проверь кадрирование на всём ролике."
                if render["layout"] == "split"
                else "⚠️ Автовебка не подтверждена: сохранён полный кадр. Проверь ролик."
            )
        lines += [
            f"#{render['id']} · {clip['broadcaster_name']} · {clip['view_count']} просм. · "
            f"{float(render['duration']):.0f} с",
            f"Хук: {render['hook_text'] or '—'}",
        ]
        options = db.hook_options(render)[:3]
        if options:
            lines.append("Варианты:")
            lines += [f"{i + 1}) {option}" for i, option in enumerate(options)]
        lines.append(clip["url"])
        return "\n".join(lines)[:1000]

    def send_render(self, render_id: int) -> None:
        render = db.get_render(self.conn, render_id)
        if render is None:
            raise RuntimeError(f"render #{render_id} not found")
        clip = db.get_clip(self.conn, render["clip_id"])
        path = Path(render["path"])
        if path.stat().st_size > TELEGRAM_UPLOAD_LIMIT:
            path = make_preview(
                path,
                self.settings.work_dir / "previews" / f"{render_id}.mp4",
                self.settings.ffmpeg_bin,
            )
        message = self.tg.send_video(
            self.owner,
            path,
            self.caption(render, clip),
            self.keyboard(render),
            width=1080,
            height=1920,
            duration=round(float(render["duration"])),
        )
        db.update_render(
            self.conn,
            render_id,
            status=RenderStatus.SENT,
            telegram_message_id=(message or {}).get("message_id"),
        )
        db.log_event(self.conn, "review_sent", clip_id=render["clip_id"], render_id=render_id)

    def send_pending(self, limit: int = 20) -> int:
        renders = db.list_renders(self.conn, statuses=[RenderStatus.DRAFT], limit=limit)
        for render in renders:
            self.send_render(render["id"])
        return len(renders)

    # --- incoming ----------------------------------------------------------------------

    def handle_update(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            self._on_callback(update["callback_query"])
        elif "message" in update:
            self._on_message(update["message"])

    def _clear(self, chat_id: int, message_id: int | None) -> None:
        if message_id is None:
            return
        try:
            self.tg.clear_buttons(chat_id, message_id)
        except Exception as exc:  # noqa: BLE001 - old messages can't be edited; not fatal
            log.info("could not clear buttons: %s", exc)

    def _on_callback(self, cb: dict[str, Any]) -> None:
        cb_id = str(cb.get("id", ""))
        user_id = (cb.get("from") or {}).get("id")
        if user_id != self.owner:
            log.warning("ignored button press from user %s", user_id)
            self.tg.answer_callback(cb_id, "Нет доступа")
            return
        try:
            action, render_id, extra = parse_callback(str(cb.get("data") or ""))
        except ValueError:
            self.tg.answer_callback(cb_id, "Не понял кнопку")
            return
        render = db.get_render(self.conn, render_id)
        if render is None:
            self.tg.answer_callback(cb_id, "Ролик не найден")
            return
        message = cb.get("message") or {}
        chat_id = int((message.get("chat") or {}).get("id", self.owner))
        message_id = message.get("message_id")
        status = RenderStatus(render["status"])

        if action == "a":
            self._approve(cb_id, render, status, chat_id, message_id)
        elif action == "r":
            if status in (RenderStatus.APPROVED, RenderStatus.POSTED):
                self.tg.answer_callback(cb_id, "Уже одобрено")
                return
            db.update_render(
                self.conn, render_id, status=RenderStatus.REJECTED, decided_at=db.utcnow()
            )
            db.log_event(self.conn, "rejected", clip_id=render["clip_id"], render_id=render_id)
            self._clear(chat_id, message_id)
            self.tg.answer_callback(cb_id, "Отклонено")
        elif action == "e":
            if status not in OPEN:
                self.tg.answer_callback(cb_id, "Эта версия уже неактуальна")
                return
            self.pending_edit[chat_id] = render_id
            self.tg.answer_callback(cb_id, "")
            self.tg.send_message(
                chat_id,
                f"Пришли новый хук для #{render_id} одним сообщением.\nФормат: Кто: фраза",
                {"force_reply": True},
            )
        elif action == "h":
            options = db.hook_options(render)
            if status not in OPEN:
                self.tg.answer_callback(cb_id, "Эта версия уже неактуальна")
                return
            if extra is None or not 0 <= extra < len(options):
                self.tg.answer_callback(cb_id, "Нет такого варианта")
                return
            self.tg.answer_callback(cb_id, "Перерисовываю…")
            self._clear(chat_id, message_id)
            self._rerender_and_send(chat_id, render_id, options[extra])
        elif action == "p":
            if status == RenderStatus.POSTED:
                self.tg.answer_callback(cb_id, "Уже отмечено")
                return
            if status != RenderStatus.APPROVED:
                self.tg.answer_callback(cb_id, "Сначала одобри ролик")
                return
            db.update_render(
                self.conn, render_id, status=RenderStatus.POSTED, posted_at=db.utcnow()
            )
            db.log_event(self.conn, "posted", clip_id=render["clip_id"], render_id=render_id)
            self._clear(chat_id, message_id)
            self.tg.answer_callback(cb_id, "Отмечено как опубликованное")

    def _approve(
        self,
        cb_id: str,
        render: sqlite3.Row,
        status: RenderStatus,
        chat_id: int,
        message_id: int | None,
    ) -> None:
        render_id = render["id"]
        if status in (RenderStatus.APPROVED, RenderStatus.POSTED):
            self.tg.answer_callback(cb_id, "Уже одобрено")
            return
        if status not in OPEN:
            self.tg.answer_callback(cb_id, "Эта версия уже неактуальна")
            return
        self.tg.answer_callback(cb_id, "Одобрено")
        path = self.export(render_id)
        db.update_render(
            self.conn,
            render_id,
            status=RenderStatus.APPROVED,
            decided_at=db.utcnow(),
            export_path=str(path),
        )
        db.log_event(self.conn, "approved", clip_id=render["clip_id"], render_id=render_id)
        self._clear(chat_id, message_id)
        caption_file = path.with_suffix(".txt")
        caption = caption_file.read_text(encoding="utf-8") if caption_file.is_file() else ""
        if path.stat().st_size > TELEGRAM_UPLOAD_LIMIT:
            self.tg.send_message(chat_id, f"Файл больше 50 МБ, он лежит в {path}\n\n{caption}")
            return
        self.tg.send_document(
            chat_id,
            path,
            caption,
            {"inline_keyboard": [[{"text": "📤 Опубликовал", "callback_data": f"p:{render_id}"}]]},
        )

    def _rerender_and_send(self, chat_id: int, render_id: int, hook_text: str) -> None:
        try:
            new_id = self.rerender(render_id, hook_text)
        except Exception as exc:  # noqa: BLE001 - report any failure back to the reviewer
            log.exception("re-render of #%s failed", render_id)
            self.tg.send_message(chat_id, f"Не получилось перерисовать #{render_id}: {exc}")
            return
        self.send_render(new_id)

    def _on_message(self, msg: dict[str, Any]) -> None:
        chat_id = (msg.get("chat") or {}).get("id")
        user_id = (msg.get("from") or {}).get("id")
        if user_id != self.owner or chat_id != self.owner:
            log.info("ignored message from user %s in chat %s", user_id, chat_id)
            return
        text = (msg.get("text") or "").strip()
        if not text:
            return
        if text.startswith("/"):
            self._command(int(chat_id), text.split()[0].split("@")[0])
            return
        if text == "🎬 Создать ролик":
            self._request_new_clip(int(chat_id))
            return
        if text == "📋 Очередь":
            self._command(int(chat_id), "/pending")
            return
        if text == "📊 Статус":
            self._command(int(chat_id), "/status")
            return
        render_id = self.pending_edit.pop(int(chat_id), None)
        if render_id is None:
            self.tg.send_message(chat_id, HELP, MAIN_MENU)
            return
        hook = " ".join(text.split())[:80]
        self.tg.send_message(chat_id, f"Перерисовываю #{render_id} с новым хуком…")
        self._rerender_and_send(int(chat_id), render_id, hook)

    def _request_new_clip(self, chat_id: int) -> None:
        """Queue one local run without blocking the Telegram poll loop."""
        from clipfactory.run_control import PipelineBusy

        if self.start_pipeline is None:
            self.tg.send_message(chat_id, "Ручной запуск пока не настроен.")
            return
        try:
            self.start_pipeline()
        except PipelineBusy:
            self.tg.send_message(chat_id, "⏳ Обработка уже запущена. Новую пока не начинаю.")
        except Exception:  # noqa: BLE001 - do not leak local file paths or secrets to Telegram
            log.exception("could not start manual pipeline")
            self.tg.send_message(
                chat_id, "❌ Не удалось запустить обработку. Проверь data/logs/bot.log."
            )
        else:
            self.tg.send_message(
                chat_id,
                "🎬 Запускаю создание одного ролика на Mac. "
                "Когда он будет готов, пришлю сюда на проверку. "
                "Если новых подходящих клипов нет, ролик не появится.",
            )

    def _command(self, chat_id: int, command: str) -> None:
        if command in ("/start", "/help"):
            self.tg.send_message(chat_id, HELP, MAIN_MENU)
        elif command == "/new":
            self._request_new_clip(chat_id)
        elif command == "/pending":
            if self.send_pending() == 0:
                self.tg.send_message(chat_id, "Новых роликов нет.")
        elif command == "/status":
            counts = db.status_counts(self.conn)
            clips = ", ".join(f"{k}: {v}" for k, v in sorted(counts["clips"].items())) or "—"
            renders = ", ".join(f"{k}: {v}" for k, v in sorted(counts["renders"].items())) or "—"
            self.tg.send_message(chat_id, f"Клипы — {clips}\nРолики — {renders}")
        else:
            self.tg.send_message(chat_id, HELP, MAIN_MENU)

    # --- loop --------------------------------------------------------------------------

    def run(self, *, poll_timeout: int = 50, max_cycles: int | None = None) -> None:
        offset = int(db.kv_get(self.conn, "telegram_offset") or 0)
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            cycles += 1
            try:
                updates = self.tg.get_updates(offset, timeout=poll_timeout)
            except Exception as exc:  # noqa: BLE001 - network blips: wait and retry
                log.warning("getUpdates failed: %s", exc)
                time.sleep(5)
                continue
            for update in updates:
                offset = int(update["update_id"]) + 1
                db.kv_set(self.conn, "telegram_offset", str(offset))
                try:
                    self.handle_update(update)
                except Exception:  # noqa: BLE001 - one bad update must not stop the bot
                    log.exception("update %s failed", update.get("update_id"))
