import os
from pyrogram import Client
from bot.config import settings
from bot.utils.logger import log

userbot = None

if settings.STRING_SESSION:
    # Save a local session file so peer cache persists across restarts.
    # This prevents "Peer id invalid" errors after bot restarts.
    os.makedirs("data", exist_ok=True)
    userbot = Client(
        "data/leech_userbot",      # session file saved at data/leech_userbot.session
        api_id=settings.API_ID,
        api_hash=settings.API_HASH,
        session_string=settings.STRING_SESSION,
        # in_memory removed — lets Pyrogram write peer cache to disk
    )
else:
    log.warning("[Userbot] STRING_SESSION not set. Leech feature will be disabled.")
