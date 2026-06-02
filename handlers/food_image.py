"""
handlers/food_image.py — Handle photo messages for food analysis
"""
import logging
from datetime import datetime

import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import config
from config import progress_bar, get_default_meal_type
from models.nutrition import NutritionEntry
from services.gemini import analyze_food_image
from services.sheets import sheets_service
from handlers.water import water_quick_keyboard

logger = logging.getLogger(__name__)

MEAL_TYPES = ["Breakfast", "Lunch", "Dinner", "Snack"]


def meal_type_keyboard_img(img_key: str) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(m, callback_data=f"img_meal:{m}:{img_key}")
        for m in MEAL_TYPES
    ]
    return InlineKeyboardMarkup([buttons[:2], buttons[2:]])


async def handle_food_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle a photo message — download, send to Gemini vision, ask meal type."""
    processing_msg = await update.message.reply_text("📸 Analysing your meal photo…")

    try:
        photo = update.message.photo[-1]  # highest resolution
        file = await context.bot.get_file(photo.file_id)
        image_bytes = await file.download_as_bytearray()

        result = await analyze_food_image(bytes(image_bytes))
    except RuntimeError as e:
        await processing_msg.delete()
        await update.message.reply_text(f"⚠️ {e}")
        return
    except Exception as e:
        logger.error(f"Image analysis error: {e}")
        await processing_msg.delete()
        await update.message.reply_text(
            "❌ Couldn't analyse the photo. Make sure it's a clear image of food."
        )
        return

    await processing_msg.delete()

    if result.get("error") == "no_food_detected":
        await update.message.reply_text(
            f"🤔 {result.get('message', 'No food detected in this image.')}"
        )
        return

    profile = await sheets_service.get_profile()
    tz_str = profile.get("timezone") or config.DEFAULT_TIMEZONE
    tz = pytz.timezone(tz_str)
    now = datetime.now(tz)
    suggested_meal = get_default_meal_type(now.hour)
    img_key = str(now.timestamp())[:10]

    context.user_data["pending_img_food"] = result
    context.user_data["pending_img_key"] = img_key

    conf = result.get("confidence", "high")
    conf_note = "\n⚠️ _Visual estimate — /edit to correct_" if conf == "low" else ""

    items = result.get("identified_items", [])
    items_str = ", ".join(items) if items else result.get("food_name", "")

    preview = (
        f"📸 *{result.get('food_name', 'Meal')}*\n"
        f"_Detected: {items_str}_\n"
        f"🔥 {result.get('calories', 0)} kcal  |  "
        f"💪 {result.get('protein_g', 0):.1f}g protein  |  "
        f"🌾 {result.get('carbs_g', 0):.1f}g carbs  |  "
        f"🥑 {result.get('fat_g', 0):.1f}g fat{conf_note}\n\n"
        f"Select meal type (suggested: *{suggested_meal}*):"
    )
    await update.message.reply_text(
        preview,
        parse_mode="Markdown",
        reply_markup=meal_type_keyboard_img(img_key),
    )


async def img_meal_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback when user selects meal type for a photo-analysed food."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) < 2:
        return
    meal_type = parts[1]

    result = context.user_data.get("pending_img_food")
    if not result:
        await query.edit_message_text("❌ Session expired. Please re-send the photo.")
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

    # Today totals
    today_entries = await sheets_service.get_food_entries_for_date(date_str)
    total_cal = sum(e.calories for e in today_entries)
    cal_goal = int(profile.get("daily_calorie_limit") or config.DEFAULT_CALORIE_LIMIT)
    bar = progress_bar(total_cal, cal_goal)
    pct = int((total_cal / cal_goal) * 100) if cal_goal else 0

    conf = result.get("confidence", "high")
    conf_note = "\n⚠️ _Visual estimate — /edit to correct_" if conf == "low" else ""

    confirmation = (
        f"✅ Logged: *{entry.food_name}*\n"
        f"🔥 {entry.calories} kcal  |  💪 {entry.protein_g:.1f}g protein  |  "
        f"🌾 {entry.carbs_g:.1f}g carbs  |  🥑 {entry.fat_g:.1f}g fat{conf_note}\n"
        f"──────────────────────────────\n"
        f"Today so far: *{total_cal} / {cal_goal} kcal*  {bar}  {pct}%"
    )
    await query.edit_message_text(confirmation, parse_mode="Markdown")
    await query.message.reply_text("💧 Log some water?", reply_markup=water_quick_keyboard())

    context.user_data.pop("pending_img_food", None)
    context.user_data.pop("pending_img_key", None)
