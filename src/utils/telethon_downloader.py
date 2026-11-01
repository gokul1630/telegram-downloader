import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardMarkup, Message as BotMessage
from telegram.error import TelegramError
from telethon import TelegramClient

from ..models import DownloadFile
from .env import env

logger = logging.getLogger(__name__)


class DownloadCancelled(Exception):
    pass


def format_eta(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02}:{minutes:02}:{seconds:02}"
    return f"{minutes:02}:{seconds:02}"


async def initialize_telethon_client(application) -> None:
    session_path = Path(env.TELETHON_SESSION_PATH)
    if not Path(f"{session_path}.session").exists():
        raise RuntimeError(
            f"Telethon session is missing at {session_path}.session. "
            "Create it in the bot's mounted downloads volume with "
            "`docker compose -f docker-compose.prod.yml run --rm --no-deps bot "
            "uv run python scripts/create_telethon_session.py`."
        )

    client = TelegramClient(
        str(session_path),
        env.TELEGRAM_API_ID,
        env.TELEGRAM_API_HASH,
        receive_updates=False,
    )
    await client.connect()

    try:
        if not await client.is_user_authorized():
            raise RuntimeError(
                "Telethon session has expired. Regenerate it with "
                "`uv run python scripts/create_telethon_session.py`."
            )

        account = await client.get_me()
        if str(account.id) != env.USER_ID:
            raise RuntimeError(
                "Telethon session account does not match configured USER_ID."
            )

        bot = await application.bot.get_me()
        if not bot.username:
            raise RuntimeError("The bot must have a username to resolve private chats.")

        # Cache chat access hashes for Bot API chat IDs used by the downloader.
        await client.get_dialogs()
        application.bot_data["telethon_client"] = client
        application.bot_data["telethon_bot_username"] = bot.username
    except Exception:
        await client.disconnect()
        raise


async def shutdown_telethon_client(application) -> None:
    client = application.bot_data.pop("telethon_client", None)
    if client is not None:
        await client.disconnect()


async def _edit_progress_message(
    message: BotMessage,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramError:
        logger.warning("Could not update download progress message", exc_info=True)


def _original_file_name(message: BotMessage) -> str | None:
    for attribute in ("document", "video", "audio", "animation"):
        media = getattr(message, attribute, None)
        file_name = getattr(media, "file_name", None)
        if file_name:
            return os.path.basename(file_name.replace("\\", "/"))
    return None


async def _find_recent_media_message(
    client: TelegramClient,
    entity,
    source: BotMessage,
    file: DownloadFile,
) -> Any | None:
    target_name = _original_file_name(source)
    matches = []

    for candidate in await client.get_messages(entity, limit=100):
        if candidate is None or candidate.media is None or candidate.date is None:
            continue

        time_delta = abs((candidate.date - source.date).total_seconds())
        if time_delta > 600:
            continue

        candidate_file = candidate.file
        candidate_name = getattr(candidate_file, "name", None)
        candidate_size = getattr(candidate_file, "size", None)
        name_matches = bool(target_name and candidate_name == target_name)
        size_matches = bool(file.file_size and candidate_size == file.file_size)

        if target_name and candidate_name and candidate_name != target_name:
            continue
        if file.file_size and candidate_size and candidate_size != file.file_size:
            continue
        if not (name_matches or size_matches):
            continue

        matches.append(
            (
                (int(name_matches), int(size_matches), -time_delta),
                candidate,
            )
        )

    if not matches:
        return None

    matches.sort(key=lambda match: match[0], reverse=True)
    best_score = matches[0][0]
    best_matches = [candidate for score, candidate in matches if score == best_score]
    return best_matches[0] if len(best_matches) == 1 else None


async def download_message_with_progress(
    client: TelegramClient,
    bot_username: str,
    source: BotMessage,
    destination_path: str,
    file: DownloadFile,
    progress_message: BotMessage,
    cancel_event: asyncio.Event,
    cancel_markup: InlineKeyboardMarkup,
) -> tuple[float, float]:
    if os.path.exists(destination_path):
        raise FileExistsError(f"File already exists: {destination_path}")

    lookup_candidates = []
    forward_origin = getattr(source, "forward_origin", None)
    origin_chat = getattr(forward_origin, "chat", None)
    origin_message_id = getattr(forward_origin, "message_id", None)
    if origin_chat is not None and origin_message_id is not None:
        origin_entity = getattr(origin_chat, "username", None) or origin_chat.id
        lookup_candidates.append((origin_entity, origin_message_id))

    source_entity = bot_username if source.chat_id > 0 else source.chat_id
    lookup_candidates.append((source_entity, source.message_id))

    source_message = None
    lookup_errors = []
    history_scan_attempted = False
    for entity, message_id in lookup_candidates:
        if cancel_event.is_set():
            raise DownloadCancelled()
        try:
            source_message = await client.get_messages(entity, ids=message_id)
        except Exception as error:
            lookup_errors.append(f"chat {entity}, message {message_id}: {error}")
            continue
        if source_message is not None and source_message.media is not None:
            break

    if source_message is None or source_message.media is None:
        if cancel_event.is_set():
            raise DownloadCancelled()
        history_scan_attempted = True
        try:
            source_message = await _find_recent_media_message(
                client,
                source_entity,
                source,
                file,
            )
        except Exception as error:
            lookup_errors.append(f"recent history in chat {source_entity}: {error}")

    if cancel_event.is_set():
        raise DownloadCancelled()

    if source_message is None or source_message.media is None:
        locations = ", ".join(
            f"chat {entity}/message {message_id}"
            for entity, message_id in lookup_candidates
        )
        details = "; ".join(lookup_errors)
        if history_scan_attempted:
            details = "; ".join(
                part
                for part in (
                    details,
                    "no unique match by filename/size within the latest 100 "
                    "messages near the source timestamp",
                )
                if part
            )
        if not details:
            details = "message was missing or had no media"
        raise ValueError(
            "Telethon could not find media for the source message. "
            f"Tried {len(lookup_candidates)} location(s) ({locations}); "
            f"{details}. "
            "Ensure the signed-in account can access the source chat."
        )

    progress = {"received": 0, "total": file.file_size}

    def on_progress(received: int, total: int) -> None:
        progress["received"] = received
        if total:
            progress["total"] = total

    started_at = time.monotonic()
    download_task = asyncio.create_task(
        client.download_media(
            source_message,
            file=destination_path,
            progress_callback=on_progress,
        )
    )

    try:
        last_reported_bytes = -1
        while not download_task.done():
            await asyncio.wait({download_task}, timeout=1.0)
            if cancel_event.is_set():
                download_task.cancel()
                await asyncio.gather(download_task, return_exceptions=True)
                raise DownloadCancelled()
            received = progress["received"]
            total = progress["total"]
            elapsed = max(time.monotonic() - started_at, 0.001)
            speed = received / elapsed
            eta = (total - received) / speed if total and speed else None
            percent = received / total * 100 if total else 0

            if received != last_reported_bytes:
                total_display = f"{total / (1024 * 1024):.2f}" if total else "unknown"
                eta_display = format_eta(eta) if eta is not None else "calculating..."
                await _edit_progress_message(
                    progress_message,
                    f"⬇️ Downloading {file.file_name}\n"
                    f"{received / (1024 * 1024):.2f} / {total_display} MB "
                    f"({percent:.1f}%)\n"
                    f"Speed: {speed / (1024 * 1024):.2f} MB/s\n"
                    f"Elapsed: {format_eta(elapsed)}\n"
                    f"ETA: {eta_display}",
                    reply_markup=cancel_markup,
                )
                last_reported_bytes = received

        result = await download_task
        if result is None:
            raise RuntimeError("Telethon returned no downloaded file.")
    except asyncio.CancelledError:
        download_task.cancel()
        await asyncio.gather(download_task, return_exceptions=True)
        if os.path.exists(destination_path):
            try:
                os.remove(destination_path)
            except OSError:
                logger.exception("Could not remove partial download")
        raise
    except Exception:
        download_task.cancel()
        if os.path.exists(destination_path):
            try:
                os.remove(destination_path)
            except OSError:
                logger.exception("Could not remove partial download")
        raise

    elapsed = max(time.monotonic() - started_at, 0.001)
    received = progress["received"] or os.path.getsize(destination_path)
    return elapsed, received / elapsed
