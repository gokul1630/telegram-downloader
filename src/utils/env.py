import logging

from dotenv import load_dotenv
from pydantic import ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    BOT_TOKEN: str
    LOCAL_BOT_API_URL: str
    BOT_API_DIR: str
    DOWNLOAD_TO_DIR: str
    USER_ID: str = ""
    USER_IDS: str = ""
    CHAT_ID: str = ""
    CHAT_IDS: str = ""
    TELEGRAM_API_ID: int
    TELEGRAM_API_HASH: str
    TELETHON_SESSION_PATH: str = "downloads/telethon_user"

    @property
    def allowed_user_ids(self) -> set[str]:
        return self._parse_id_list(self.USER_ID, self.USER_IDS)

    @property
    def allowed_chat_ids(self) -> set[str]:
        return self._parse_id_list(self.CHAT_ID, self.CHAT_IDS)

    @staticmethod
    def _parse_id_list(*values: str) -> set[str]:
        return {
            value.strip()
            for entry in values
            for value in entry.split(",")
            if value.strip()
        }

    @model_validator(mode="after")
    def require_access_lists(self):
        if not self.allowed_user_ids:
            raise ValueError("Set USER_ID or USER_IDS to authorize at least one user.")
        if not self.allowed_chat_ids:
            raise ValueError("Set CHAT_ID or CHAT_IDS to authorize at least one chat.")
        return self


logger.info("Loading environment variables")

try:
    env = Settings()
except ValidationError as e:
    logger.error("Environment variables validation error: %s", e)
    exit(1)
