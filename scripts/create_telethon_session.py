import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient


async def main() -> None:
    load_dotenv()
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    allowed_user_ids = {
        user_id.strip()
        for value in (os.getenv("USER_ID", ""), os.getenv("USER_IDS", ""))
        for user_id in value.split(",")
        if user_id.strip()
    }
    if not api_id or not api_hash or not allowed_user_ids:
        raise RuntimeError(
            "Set TELEGRAM_API_ID, TELEGRAM_API_HASH, and USER_ID or USER_IDS in .env first."
        )

    session_path = Path(
        os.getenv("TELETHON_SESSION_PATH", "downloads/telethon_user")
    )
    session_path.parent.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(str(session_path), int(api_id), api_hash)
    await client.start()
    try:
        account = await client.get_me()
        if str(account.id) not in allowed_user_ids:
            raise RuntimeError(
                f"Signed in as account {account.id}, which is not in USER_ID/USER_IDS."
            )
        print(f"Telethon session created for Telegram account {account.id}.")
        print(f"Session file: {session_path}.session")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
