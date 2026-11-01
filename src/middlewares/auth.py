from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes

from ..utils import env

CHAT_ID = env.CHAT_ID


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
        if str(update.effective_chat.id) != CHAT_ID:
            await update.effective_message.reply_text(
                "Unauthorized chat.\n"
                "Please verify the value of `CHAT_ID` in environment variables.",
                parse_mode="markdown",
            )
            return
        return await func(update, context)

    return wrapper
