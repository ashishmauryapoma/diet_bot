"""
handlers/auth.py — Single-user password authentication
"""
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, Set

import bcrypt
from dotenv import set_key, dotenv_values
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

import config

logger = logging.getLogger(__name__)

# ── In-memory state ────────────────────────────────────────────────────────────
verified_sessions: Set[int] = set()
failed_attempts: Dict[int, list] = {}   # chat_id → list of datetime
_pending_password_setup: Set[int] = set()  # chat_ids awaiting first password input
_pending_password_verify: Set[int] = set()  # chat_ids awaiting password verification

ENV_FILE = ".env"

# ── Helpers ─────────────────────────────────────────────────────────────────────

def _reload_env() -> None:
    """Reload AUTHORIZED_USER_ID and BOT_PASSWORD_HASH from .env at runtime."""
    vals = dotenv_values(ENV_FILE)
    config.AUTHORIZED_USER_ID = vals.get("AUTHORIZED_USER_ID") or None
    config.BOT_PASSWORD_HASH  = vals.get("BOT_PASSWORD_HASH")  or None


def _save_env_value(key: str, value: str) -> None:
    set_key(ENV_FILE, key, value)
    _reload_env()


def is_locked_out(chat_id: int) -> bool:
    attempts = failed_attempts.get(chat_id, [])
    if len(attempts) >= config.MAX_FAILED_ATTEMPTS:
        last = attempts[-1]
        if datetime.now() - last < timedelta(minutes=config.LOCKOUT_MINUTES):
            return True
        else:
            failed_attempts[chat_id] = []  # reset after lockout period
    return False


def record_failed_attempt(chat_id: int) -> int:
    """Record a failed attempt and return number of attempts so far."""
    failed_attempts.setdefault(chat_id, []).append(datetime.now())
    return len(failed_attempts[chat_id])


def clear_failed_attempts(chat_id: int) -> None:
    failed_attempts.pop(chat_id, None)


# ── Auth check (used by auth_required decorator) ────────────────────────────────

def get_auth_state(user_id: int) -> str:
    """
    Returns:
      'verified'        — user is allowed and session is active
      'needs_password'  — authorized user but needs to re-authenticate
      'first_setup'     — no owner yet, prompt to create password
      'denied'          — user_id does not match owner
    """
    _reload_env()
    if config.AUTHORIZED_USER_ID is None:
        return "first_setup"
    if str(user_id) == str(config.AUTHORIZED_USER_ID):
        if user_id in verified_sessions:
            return "verified"
        return "needs_password"
    return "denied"


# ── Handler functions ───────────────────────────────────────────────────────────

async def handle_first_setup_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask new user to set a password (first-time setup)."""
    chat_id = update.effective_chat.id
    _pending_password_setup.add(chat_id)
    await update.message.reply_text(
        "👋 Welcome! This is a private bot.\n\n"
        "🔐 Set your access password (you'll need this to log in from any device):"
    )


async def handle_setup_password_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Called when user sends a message and we're awaiting first-time password.
    Returns True if handled, False otherwise.
    """
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if chat_id not in _pending_password_setup:
        return False

    password = update.message.text.strip()
    if not password:
        await update.message.reply_text("❌ Password cannot be empty. Please enter a password:")
        return True

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    _save_env_value("BOT_PASSWORD_HASH", hashed)
    _save_env_value("AUTHORIZED_USER_ID", str(user_id))
    _pending_password_setup.discard(chat_id)
    verified_sessions.add(user_id)

    await update.message.reply_text(
        "✅ Password set! You're now the sole owner of this bot.\n\n"
        "Run /start to set up your nutrition profile."
    )
    return True


async def handle_verify_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask returning owner to enter password."""
    chat_id = update.effective_chat.id
    _pending_password_verify.add(chat_id)
    await update.message.reply_text("🔒 Welcome back! Enter your password to continue:")


async def handle_verify_password_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Called when user sends a message and we're awaiting password verification.
    Returns True if handled, False otherwise.
    """
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if chat_id not in _pending_password_verify:
        return False

    if is_locked_out(chat_id):
        await update.message.reply_text(
            "❌ Too many failed attempts. Try again in 30 minutes."
        )
        return True

    password = update.message.text.strip()
    _reload_env()
    stored_hash = config.BOT_PASSWORD_HASH

    if stored_hash and bcrypt.checkpw(password.encode(), stored_hash.encode()):
        clear_failed_attempts(chat_id)
        verified_sessions.add(user_id)
        _pending_password_verify.discard(chat_id)
        await update.message.reply_text(
            "✅ Verified! Resuming your tracker. Use /daily to see today's summary."
        )
    else:
        count = record_failed_attempt(chat_id)
        remaining = config.MAX_FAILED_ATTEMPTS - count
        if remaining <= 0:
            await update.message.reply_text(
                "❌ Too many failed attempts. Try again in 30 minutes."
            )
        else:
            await update.message.reply_text(
                f"❌ Wrong password. {remaining} attempt(s) remaining."
            )
    return True
