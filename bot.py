"""
bot.py — DietTrackerBot main entry point
Single-user, password-protected nutrition + water tracker
"""
import logging
from functools import wraps

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

import config
from handlers.auth import (
    get_auth_state,
    handle_first_setup_prompt,
    handle_setup_password_input,
    handle_verify_prompt,
    handle_verify_password_input,
)
from handlers.commands import (
    cmd_daily, cmd_edit, cmd_help, cmd_profile, cmd_remaining,
    cmd_start, cmd_water, cmd_weekly, generic_callback,
    profile_edit_callback, profile_field_callback,
    _tz_now, _date_str,
)
from handlers.food_image import handle_food_photo, img_meal_type_callback
from handlers.food_text import handle_food_text, meal_type_callback
from handlers.water import water_button_callback
from services.sheets import sheets_service

logging.basicConfig(
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Auth middleware ─────────────────────────────────────────────────────────────

async def auth_gate(update: Update, context) -> bool:
    """
    Gate every update through auth. Returns True if the update may proceed.
    Handles first-time setup, re-auth, and denied users inline.
    """
    user = update.effective_user
    if user is None:
        return False

    user_id = user.id
    state = get_auth_state(user_id)

    if state == "verified":
        return True

    if state == "first_setup":
        if await handle_setup_password_input(update, context):
            return False
        await handle_first_setup_prompt(update, context)
        return False

    if state == "needs_password":
        if await handle_verify_password_input(update, context):
            return False
        await handle_verify_prompt(update, context)
        return False

    # state == "denied"
    if update.message:
        await update.message.reply_text("🚫 This is a private bot. Access denied.")
    return False


def auth_required(handler):
    """Decorator to gate any handler behind auth_gate."""
    @wraps(handler)
    async def wrapper(update: Update, context, *args, **kwargs):
        if await auth_gate(update, context):
            return await handler(update, context, *args, **kwargs)
    return wrapper


# ── Wrapped handlers ───────────────────────────────────────────────────────────

@auth_required
async def _cmd_start(u, c):     return await cmd_start(u, c)

@auth_required
async def _cmd_help(u, c):      return await cmd_help(u, c)

@auth_required
async def _cmd_daily(u, c):     return await cmd_daily(u, c)

@auth_required
async def _cmd_weekly(u, c):    return await cmd_weekly(u, c)

@auth_required
async def _cmd_profile(u, c):   return await cmd_profile(u, c)

@auth_required
async def _cmd_water(u, c):     return await cmd_water(u, c)

@auth_required
async def _cmd_remaining(u, c): return await cmd_remaining(u, c)

@auth_required
async def _cmd_edit(u, c):      return await cmd_edit(u, c)

@auth_required
async def _food_text(u, c):     return await handle_food_text(u, c)

@auth_required
async def _food_photo(u, c):    return await handle_food_photo(u, c)

@auth_required
async def _callback(u, c):
    """Route all inline callbacks to the correct handler."""
    data = u.callback_query.data if u.callback_query else ""
    if data.startswith("water_"):
        return await water_button_callback(u, c)
    elif data.startswith("meal_type:"):
        return await meal_type_callback(u, c)
    elif data.startswith("img_meal:"):
        return await img_meal_type_callback(u, c)
    else:
        return await generic_callback(u, c)


# ── Scheduled reminders ────────────────────────────────────────────────────────

async def _push_message(app: Application, text: str) -> None:
    if config.AUTHORIZED_USER_ID:
        try:
            await app.bot.send_message(
                chat_id=int(config.AUTHORIZED_USER_ID),
                text=text,
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Failed to send reminder: {e}")


def _setup_scheduler(app: Application) -> AsyncIOScheduler:

    async def reminder_morning():
        await _push_message(app, "🌅 Good morning! Ready to track today? Log your breakfast when you're ready 🍳")

    async def reminder_water_afternoon():
        if not config.AUTHORIZED_USER_ID:
            return
        profile = await sheets_service.get_profile()
        tz_str = profile.get("timezone") or config.DEFAULT_TIMEZONE
        date_str = _date_str(_tz_now(tz_str))
        entries = await sheets_service.get_water_entries_for_date(date_str)
        total = sum(e.amount_ml for e in entries)
        if total < 1000:
            await _push_message(app, f"💧 Stay hydrated! You've only had *{total} ml* so far today.")

    async def reminder_food_evening():
        if not config.AUTHORIZED_USER_ID:
            return
        profile = await sheets_service.get_profile()
        tz_str = profile.get("timezone") or config.DEFAULT_TIMEZONE
        date_str = _date_str(_tz_now(tz_str))
        entries = await sheets_service.get_food_entries_for_date(date_str)
        if len(entries) < 2:
            await _push_message(app, "🍽️ You haven't logged much today. What did you eat? Send me a description or photo!")

    async def reminder_water_night():
        if not config.AUTHORIZED_USER_ID:
            return
        profile = await sheets_service.get_profile()
        tz_str = profile.get("timezone") or config.DEFAULT_TIMEZONE
        date_str = _date_str(_tz_now(tz_str))
        water_goal = int(profile.get("water_goal_ml") or config.DEFAULT_WATER_GOAL_ML)
        entries = await sheets_service.get_water_entries_for_date(date_str)
        total = sum(e.amount_ml for e in entries)
        if total < 2000:
            await _push_message(app, f"💧 *{total} ml* logged. Push to hit your *{water_goal} ml* goal tonight!")

    async def reminder_daily_summary():
        if not config.AUTHORIZED_USER_ID:
            return
        profile = await sheets_service.get_profile()
        tz_str = profile.get("timezone") or config.DEFAULT_TIMEZONE
        date_str = _date_str(_tz_now(tz_str))
        food_e  = await sheets_service.get_food_entries_for_date(date_str)
        water_e = await sheets_service.get_water_entries_for_date(date_str)
        total_cal   = sum(e.calories  for e in food_e)
        total_prot  = sum(e.protein_g for e in food_e)
        total_water = sum(w.amount_ml for w in water_e)
        await _push_message(
            app,
            f"📊 *Day wrap-up:* {total_cal} kcal · {total_prot:.0f}g protein · 💧 {total_water} ml\n"
            "See you tomorrow! 👋"
        )

    # Use default timezone for scheduler init; each job reads the real timezone
    # from the sheet at fire-time, so this only affects the cron wall-clock.
    scheduler = AsyncIOScheduler(timezone=pytz.timezone(config.DEFAULT_TIMEZONE))
    scheduler.add_job(reminder_morning,         "cron", hour=8,  minute=0)
    scheduler.add_job(reminder_water_afternoon, "cron", hour=15, minute=0)
    scheduler.add_job(reminder_food_evening,    "cron", hour=20, minute=0)
    scheduler.add_job(reminder_water_night,     "cron", hour=21, minute=0)
    scheduler.add_job(reminder_daily_summary,   "cron", hour=23, minute=55)
    return scheduler


# ── Application setup ──────────────────────────────────────────────────────────

def build_app() -> Application:
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",     _cmd_start))
    app.add_handler(CommandHandler("help",      _cmd_help))
    app.add_handler(CommandHandler("daily",     _cmd_daily))
    app.add_handler(CommandHandler("weekly",    _cmd_weekly))
    app.add_handler(CommandHandler("profile",   _cmd_profile))
    app.add_handler(CommandHandler("water",     _cmd_water))
    app.add_handler(CommandHandler("remaining", _cmd_remaining))
    app.add_handler(CommandHandler("edit",      _cmd_edit))

    app.add_handler(MessageHandler(filters.PHOTO, _food_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _food_text))
    app.add_handler(CallbackQueryHandler(_callback))

    return app


def main() -> None:
    app = build_app()
    scheduler = _setup_scheduler(app)
    scheduler.start()
    logger.info("DietTrackerBot starting (polling mode)…")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
