"""
handlers/food_text.py — Handle text food descriptions via Gemini
"""
import logging
from datetime import datetime

import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import config
from config import progress_bar, get_default_meal_type
from models.nutrition import NutritionEntry
from services.gemini import analyze_food_text
from services.sheets import sheets_service
from handlers.water import water_quick_keyboard

logger = logging.getLogger(__name__)

MEAL_TYPES = ["Breakfast", "Lunch", "Dinner", "Snack"]


def meal_type_keyboard(food_key: str) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(m, callback_data=f"meal_type:{m}:{food_key}")
        for m in MEAL_TYPES
    ]
    return InlineKeyboardMarkup([buttons[:2], buttons[2:]])


async def handle_food_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main handler for free-text food descriptions."""
    text = update.message.text.strip()
    if not text:
        return

    # Skip if it's a command
    if text.startswith("/"):
        return

    # Check for custom water input first (delegated)
    from handlers.water import handle_custom_water_input
    if await handle_custom_water_input(update, context):
        return

    # Check if awaiting edit correction
    if context.user_data.get("awaiting_edit_correction"):
        from handlers.commands import handle_edit_correction
        await handle_edit_correction(update, context)
        return

    # Check if in profile edit mode
    if context.user_data.get("profile_edit_field"):
        from handlers.commands import handle_profile_edit_input
        await handle_profile_edit_input(update, context)
        return

    # Check if in onboarding
    if context.user_data.get("onboarding_step"):
        from handlers.commands import handle_onboarding_input
        await handle_onboarding_input(update, context)
        return

    processing_msg = await update.message.reply_text("🔍 Analysing your food…")

    try:
        result = await analyze_food_text(text)
    except RuntimeError as e:
        await processing_msg.delete()
        await update.message.reply_text(f"⚠️ {e}")
        return
    except Exception as e:
        logger.error(f"Gemini text error: {e}")
        await processing_msg.delete()
        await update.message.reply_text(
            "❌ Couldn't understand that food. Try rephrasing, e.g.\n"
            "\"2 rotis with dal and a glass of milk\""
        )
        return

    if "error" in result:
        await processing_msg.delete()
        await update.message.reply_text(f"🤔 {result.get('message', 'Could not analyse food.')}")
        return

    await processing_msg.delete()

    # Store pending entry; ask for meal type
    context.user_data["pending_food"] = result
    context.user_data["pending_food_text"] = text

    profile = await sheets_service.get_profile()
    tz_str = profile.get("timezone") or config.DEFAULT_TIMEZONE
    tz = pytz.timezone(tz_str)
    now = datetime.now(tz)
    suggested_meal = get_default_meal_type(now.hour)

    food_key = str(now.timestamp())[:10]
    context.user_data["pending_food_key"] = food_key

    conf = result.get("data_confidence", "high")
    conf_note = " ⚠️ Low confidence — use /edit to correct." if conf == "low" else ""

    preview = (
        f"🍽️ *{result.get('food_name', text)}*\n"
        f"🔥 {result.get('calories', 0)} kcal  |  "
        f"💪 {result.get('protein_g', 0):.1f}g protein  |  "
        f"🌾 {result.get('carbs_g', 0):.1f}g carbs  |  "
        f"🥑 {result.get('fat_g', 0):.1f}g fat{conf_note}\n\n"
        f"Select meal type (suggested: *{suggested_meal}*):"
    )
    await update.message.reply_text(
        preview,
        parse_mode="Markdown",
        reply_markup=meal_type_keyboard(food_key),
    )


async def meal_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called when user picks a meal type for pending food."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) < 2:
        return
    meal_type = parts[1]

    result = context.user_data.get("pending_food")
    if not result:
        await query.edit_message_text("❌ Session expired. Please re-send your food description.")
        return

    profile = await sheets_service.get_profile()
    tz_str = profile.get("timezone") or config.DEFAULT_TIMEZONE
    tz = pytz.timezone(tz_str)
    now = datetime.now(tz)
    date_str = now.strftime("%d-%m-%Y")
    time_str = now.strftime("%I:%M %p")

    entry = NutritionEntry(
        date=date_str,
        time=time_str,
        meal_type=meal_type,
        food_name=result.get("food_name", ""),
        calories=int(result.get("calories", 0)),
        protein_g=float(result.get("protein_g", 0)),
        carbs_g=float(result.get("carbs_g", 0)),
        fat_g=float(result.get("fat_g", 0)),
        fiber_g=float(result.get("fiber_g", 0)),
        notes=result.get("notes"),
    )

    await sheets_service.append_food_entry(entry)

    # Today's totals
    today_entries = await sheets_service.get_food_entries_for_date(date_str)
    total_cal = sum(e.calories for e in today_entries)
    total_prot = sum(e.protein_g for e in today_entries)
    total_carbs = sum(e.carbs_g for e in today_entries)
    total_fat = sum(e.fat_g for e in today_entries)

    cal_goal = int(profile.get("daily_calorie_limit") or config.DEFAULT_CALORIE_LIMIT)
    bar = progress_bar(total_cal, cal_goal)
    pct = int((total_cal / cal_goal) * 100) if cal_goal else 0

    conf = result.get("data_confidence", "high")
    conf_note = "\n⚠️ _Visual estimate — /edit to correct_" if conf == "low" else ""

    confirmation = (
        f"✅ Logged: *{entry.food_name}*\n"
        f"🔥 {entry.calories} kcal  |  💪 {entry.protein_g:.1f}g protein  |  "
        f"🌾 {entry.carbs_g:.1f}g carbs  |  🥑 {entry.fat_g:.1f}g fat{conf_note}\n"
        f"──────────────────────────────\n"
        f"Today so far: *{total_cal} / {cal_goal} kcal*  {bar}  {pct}%\n"
        f"💪 {total_prot:.1f}g protein  |  🌾 {total_carbs:.1f}g carbs  |  🥑 {total_fat:.1f}g fat"
    )

    action_keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 See Full Day", callback_data="show_daily"),
            InlineKeyboardButton("💧 +Water", callback_data="water_500"),
            InlineKeyboardButton("✏️ Edit Last", callback_data="edit_last"),
        ]
    ])

    await query.edit_message_text(confirmation, parse_mode="Markdown")
    await query.message.reply_text(
        "💧 Log some water?",
        reply_markup=water_quick_keyboard(),
    )

    context.user_data.pop("pending_food", None)
    context.user_data.pop("pending_food_text", None)
    context.user_data.pop("pending_food_key", None)
