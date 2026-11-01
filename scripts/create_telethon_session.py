import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient


async def main() -> None:
    load_dotenv()
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    expected_user_id = os.getenv("USER_ID")
    if not api_id or not api_hash or not expected_user_id:
        raise RuntimeError(
            "Set TELEGRAM_API_ID, TELEGRAM_API_HASH, and USER_ID in .env first."
        )

    session_path = Path(
        os.getenv("TELETHON_SESSION_PATH", "downloads/telethon_user")
    )
    session_path.parent.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(str(session_path), int(api_id), api_hash)
    await client.start()
    try:
        account = await client.get_me()
        if str(account.id) != expected_user_id:
            raise RuntimeError(
                f"Signed in as account {account.id}, but USER_ID is {expected_user_id}."
            )
        print(f"Telethon session created for Telegram account {account.id}.")
        print(f"Session file: {session_path}.session")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
