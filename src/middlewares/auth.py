from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes

from ..utils import env

ALLOWED_USER_IDS = env.allowed_user_ids
ALLOWED_CHAT_IDS = env.allowed_chat_ids


def auth_required(func):
    """
    Decorator that checks if the user and chat are authorized before executing the decorated function.

    Args:
        func (callable): The function to be decorated.

    Example:
        @auth_required
        async def my_function(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            # Function implementation
    """

    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if (
            str(update.effective_user.id) not in ALLOWED_USER_IDS
            or str(update.effective_chat.id) not in ALLOWED_CHAT_IDS
        ):
            await update.effective_message.reply_text(
                "Unauthorized user or chat.\n"
                "Please verify the values of `USER_IDS`/`USER_ID` and `CHAT_IDS`/`CHAT_ID` in environment variables.",
                parse_mode="markdown",
            )
            return
        return await func(update, context)

    return wrapper
