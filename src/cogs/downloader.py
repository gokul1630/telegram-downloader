import asyncio
import logging
import math
import mimetypes
import os
import platform
import traceback
from typing import Any

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.ext import ContextTypes, filters

from ..middlewares.auth import auth_required
from ..middlewares.handlers import (
    callback_query_handler,
    command_handler,
    message_handler,
)
from ..models import DownloadFile, downloading_files
from ..utils import check_file_exists, env
from ..utils.telethon_downloader import (
    DownloadCancelled,
    download_message_with_progress,
    format_eta,
)

logger = logging.getLogger(__name__)

# Environment variables
DOWNLOAD_TO_DIR = env.DOWNLOAD_TO_DIR

DOWNLOADABLE_MEDIA_FILTER = (
    filters.Document.ALL
    | filters.PHOTO
    | filters.VIDEO
    | filters.AUDIO
    | filters.VOICE
    | filters.VIDEO_NOTE
    | filters.ANIMATION
    | filters.Sticker.ALL
)


def get_downloadable_media(message: Message) -> tuple[Any, str] | None:
    if message.document:
        return message.document, "file"
    if message.photo:
        return message.photo[-1], "photo"

    for attribute, media_type in (
        ("video", "video"),
        ("audio", "audio"),
        ("voice", "voice"),
        ("video_note", "video_note"),
        ("animation", "animation"),
        ("sticker", "sticker"),
    ):
        media = getattr(message, attribute)
        if media:
            return media, media_type
    return None


def get_media_file_details(message: Message) -> tuple[str, str, int] | None:
    downloadable_media = get_downloadable_media(message)
    if downloadable_media is None:
        return None

    media, media_type = downloadable_media
    file_name = getattr(media, "file_name", None)
    if not file_name:
        if media_type == "sticker":
            extension = (
                ".webm"
                if media.is_video
                else ".tgs"
                if media.is_animated
                else ".webp"
            )
        else:
            extension = mimetypes.guess_extension(getattr(media, "mime_type", "") or "")
            if not extension:
                extension = {
                    "photo": ".jpg",
                    "video": ".mp4",
                    "audio": ".mp3",
                    "voice": ".ogg",
                    "video_note": ".mp4",
                    "animation": ".mp4",
                }.get(media_type, "")
        file_name = f"{media_type}_{message.message_id}{extension}"

    file_name = os.path.basename(file_name.replace("\\", "/"))
    if not file_name:
        file_name = f"{media_type}_{message.message_id}"

    return media.file_id, file_name, getattr(media, "file_size", 0) or 0


@command_handler("status")
@auth_required
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send downloading files status to the user."""
    if not downloading_files:
        await update.message.reply_text("No files are being downloaded at the moment.")
        return

    status_message = "*Downloading files status:*\nPage 1\n"

    for i, file in enumerate(downloading_files.values(), start=1):
        file_status = (
            f"> 📄 *File name:*   `{file.file_name}`\n"
            f"> 💾 *File size:*   `{file.file_size_mb}`\n"
            f"> ⏰ *Start time:*   `{file.start_datetime}`\n"
            f"> ⏱ *Duration:*   `{file.current_download_duration}`\n"
            f"> 🔻 *Retries:*   `{file.download_retries}`\n"
            f"> 🔄 *Status:*   `{file.status}`\n\n"
        )
        status_message += file_status

        if i % 2 == 0 or i == len(downloading_files):
            # Add page number
            if i > 2:
                status_message = f"Page {math.ceil(i / 2)}\n" + status_message

            await context.bot.send_message(
                chat_id=update.message.chat_id,
                text=status_message,
                parse_mode="MarkdownV2",
            )
            status_message = ""
            await asyncio.sleep(0.3)


@message_handler(DOWNLOADABLE_MEDIA_FILTER)
@auth_required
async def download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask for confirmation before downloading a supported Telegram attachment."""
    logger.info("Download command received")

    message = update.message
    media_details = get_media_file_details(message)
    if media_details is None:
        return
    file_id, file_name, file_size = media_details

    try:
        check_file_exists(file_id, file_name)
    except Exception as e:
        logger.error(f"Error checking file exists: {e}")
        await message.reply_text(
            f"⛔ File already exists\\!\nError:```\n{e}```",
            parse_mode="MarkdownV2",
        )
        return

    response_message = (
        "Are you sure you want to download the file?\n\n"
        f"> 📄 *File name:*   `{file_name}`\n"
        f"> 💾 *File size:*   `{DownloadFile.convert_size(file_size)}`\n"
    )

    await context.bot.send_message(
        chat_id=message.chat_id,
        text=response_message,
        reply_to_message_id=message.message_id,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Yes", callback_data="yes"),
                    InlineKeyboardButton("No", callback_data="no"),
                ]
            ]
        ),
    )


@callback_query_handler()
@auth_required
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the confirmation button click for downloading the file."""
    logger.info("Button command received")
    query = update.callback_query

    if query.data == "cancel_download":
        progress_message = update.effective_message
        cancellation_events = context.application.bot_data.get(
            "active_download_cancellations", {}
        )
        cancel_event = cancellation_events.get(progress_message.message_id)
        await progress_message.edit_reply_markup(reply_markup=None)
        if cancel_event is None:
            await query.answer("This download is no longer active.")
        else:
            await query.answer("Cancelling download...")
            cancel_event.set()
        return

    await query.answer()

    message = update.effective_message.reply_to_message
    media_details = get_media_file_details(message)
    if media_details is None:
        await message.reply_text("This message does not contain a downloadable file.")
        return
    file_id, file_name, file_size = media_details

    if query.data == "yes":
        logger.info("Downloading file...")

        try:
            check_file_exists(file_id, file_name)
        except Exception as e:
            logger.error(f"Error checking file exists: {e}")
            await update.effective_message.edit_text(
                f"⛔ Error checking if file exists\n```\n{e}```",
                parse_mode="MarkdownV2",
            )
            return

        download_file = DownloadFile(file_id, file_name, file_size)
        downloading_files[file_id] = download_file
        progress_message = update.effective_message
        cancel_event = asyncio.Event()
        cancel_markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Cancel", callback_data="cancel_download")]]
        )
        cancellation_events = context.application.bot_data.setdefault(
            "active_download_cancellations", {}
        )
        cancellation_events[progress_message.message_id] = cancel_event
        await progress_message.edit_text(
            "🔌 Connecting to Telegram...",
            reply_markup=cancel_markup,
        )

        try:
            os.makedirs(DOWNLOAD_TO_DIR, exist_ok=True)
            move_to_path = os.path.join(DOWNLOAD_TO_DIR, file_name)
            copy_elapsed, average_speed = await download_message_with_progress(
                context.application.bot_data["telethon_client"],
                context.application.bot_data["telethon_bot_username"],
                message,
                move_to_path,
                download_file,
                progress_message,
                cancel_event,
                cancel_markup,
            )
        except DownloadCancelled:
            downloading_files.pop(file_id, None)
            await progress_message.edit_text("Download cancelled.")
            return
        except Exception as e:
            logger.error(f"Error downloading file: {e}")
            traceback.print_exc()
            downloading_files.pop(file_id, None)
            await progress_message.edit_text(
                (
                    f"⛔ Error downloading file\n"
                    f"> 📄 *File name:*   `{download_file.file_name}`\n"
                    f"> 💾 *File size:*   `{download_file.file_size_mb}`\n"
                    f"```\n{e}```"
                ),
                parse_mode="MarkdownV2",
            )
            return
        finally:
            cancellation_events.pop(progress_message.message_id, None)

        download_file.download_complete()
        download_file.move_complete()
        downloading_files.pop(file_id, None)

        if platform.system() == "Linux":
            os.chmod(move_to_path, 0o664)

        response_message = (
            f"✅ File downloaded successfully\\.\n\n"
            f"> 📄 *File name:*   `{download_file.file_name}`\n"
            f"> 📂 *File path:*   `{move_to_path}`\n"
            f"> 💾 *File size:*   `{download_file.file_size_mb}`\n"
            f"> 📈 *Average speed:*   `{average_speed / (1024 * 1024):.2f} MB/s`\n"
            f"> ⏱ *Elapsed:*   `{format_eta(copy_elapsed)}`\n"
            "> ⏳ *ETA:*   `00:00`\n"
            f"> 🔻 *Retries:*   `{download_file.download_retries}`\n"
            f"> ⏱ *Download Duration:*   `{download_file.download_duration}`\n"
            f"> ⏱ *Moving Duration:*   `{download_file.move_duration}`\n"
            f"> ⏱ *Total Duration:*   `{download_file.total_duration}`"
        )

        await progress_message.edit_text(response_message, parse_mode="MarkdownV2")
    else:
        logger.info("Download cancelled")
        await update.effective_message.edit_text("Download cancelled.")
