"""
handlers/water.py — Water intake quick-log handlers
"""
import logging
from datetime import datetime

import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CallbackQueryHandler, MessageHandler, filters

from config import progress_bar
from models.water import WaterEntry
from services.sheets import sheets_service

logger = logging.getLogger(__name__)

AWAITING_CUSTOM_WATER = "AWAITING_CUSTOM_WATER"

# ── Keyboard factory ────────────────────────────────────────────────────────────

def water_quick_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("+250 ml", callback_data="water_250"),
            InlineKeyboardButton("+500 ml", callback_data="water_500"),
            InlineKeyboardButton("+750 ml", callback_data="water_750"),
            InlineKeyboardButton("Custom", callback_data="water_custom"),
        ]
    ])


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _now_str(tz: str) -> tuple[str, str]:
    """Returns (DD-MM-YYYY, HH:MM AM/PM) in given timezone."""
    tz_obj = pytz.timezone(tz)
    now = datetime.now(tz_obj)
    return now.strftime("%d-%m-%Y"), now.strftime("%I:%M %p")


async def _get_today_water_total(date_str: str) -> int:
    entries = await sheets_service.get_water_entries_for_date(date_str)
    return sum(e.amount_ml for e in entries)


async def _log_water(amount_ml: int, note: str, tz: str) -> tuple[int, str]:
    """Log water and return (new_total, date_str)."""
    date_str, time_str = _now_str(tz)
    entry = WaterEntry(date=date_str, time=time_str, amount_ml=amount_ml, note=note)
    await sheets_service.append_water_entry(entry)
    total = await _get_today_water_total(date_str)
    return total, date_str


# ── Callback handlers ──────────────────────────────────────────────────────────

async def water_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    profile = await sheets_service.get_profile()
    tz = profile.get("timezone") or "Asia/Kolkata"
    goal = int(profile.get("water_goal_ml") or 2500)

    data = query.data
    if data == "water_custom":
        context.user_data["awaiting_custom_water"] = True
        await query.message.reply_text("💧 How many ml would you like to log? (e.g. 330)")
        return

    amount = int(data.split("_")[1])
    total, date_str = await _log_water(amount, "", tz)
    bar = progress_bar(total, goal)
    pct = int((total / goal) * 100) if goal else 0

    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        f"💧 +{amount} ml logged! Total today: *{total} / {goal} ml* {bar} {pct}%",
        parse_mode="Markdown",
        reply_markup=water_quick_keyboard(),
    )


async def handle_custom_water_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle custom water amount. Returns True if handled."""
    if not context.user_data.get("awaiting_custom_water"):
        return False

    text = update.message.text.strip()
    try:
        amount = int(float(text))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Please enter a valid number (e.g. 330):")
        return True

    context.user_data.pop("awaiting_custom_water", None)
    profile = await sheets_service.get_profile()
    tz = profile.get("timezone") or "Asia/Kolkata"
    goal = int(profile.get("water_goal_ml") or 2500)

    total, _ = await _log_water(amount, "custom", tz)
    bar = progress_bar(total, goal)
    pct = int((total / goal) * 100) if goal else 0

    await update.message.reply_text(
        f"💧 +{amount} ml logged! Total today: *{total} / {goal} ml* {bar} {pct}%",
        parse_mode="Markdown",
        reply_markup=water_quick_keyboard(),
    )
    return True
