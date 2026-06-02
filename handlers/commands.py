"""
handlers/commands.py — /start /help /daily /weekly /profile /water /remaining /edit
"""
import logging
from datetime import datetime, timedelta
from typing import List

import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import config
from config import progress_bar, DEFAULT_CALORIE_LIMIT, DEFAULT_PROTEIN_GOAL, DEFAULT_CARBS_GOAL
from config import DEFAULT_FAT_GOAL, DEFAULT_FIBER_GOAL, DEFAULT_WATER_GOAL_ML, DEFAULT_TIMEZONE
from models.nutrition import NutritionEntry
from services.sheets import sheets_service
from services.gemini import re_analyze_food
from handlers.water import water_quick_keyboard

logger = logging.getLogger(__name__)

# ── Onboarding steps ────────────────────────────────────────────────────────────
ONBOARDING_STEPS = [
    ("name",                "👋 What's your name?"),
    ("daily_calorie_limit", "🔥 Daily calorie goal (kcal)? Default is 2000:"),
    ("protein_goal_g",      "💪 Daily protein goal (grams)? Default is 120:"),
    ("carbs_goal_g",        "🌾 Daily carbs goal (grams)? Default is 250:"),
    ("fat_goal_g",          "🥑 Daily fat goal (grams)? Default is 65:"),
    ("fiber_goal_g",        "🌿 Daily fiber goal (grams)? Default is 30:"),
    ("water_goal_ml",       "💧 Daily water goal (ml)? Default is 2500:"),
    ("timezone",            "🌍 Your timezone? (e.g. Asia/Kolkata, UTC, America/New_York) Default is Asia/Kolkata:"),
]

PROFILE_FIELDS = {
    "daily_calorie_limit": ("🔥 New calorie goal (kcal):", int),
    "protein_goal_g":      ("💪 New protein goal (g):", float),
    "carbs_goal_g":        ("🌾 New carbs goal (g):", float),
    "fat_goal_g":          ("🥑 New fat goal (g):", float),
    "fiber_goal_g":        ("🌿 New fiber goal (g):", float),
    "water_goal_ml":       ("💧 New water goal (ml):", int),
    "timezone":            ("🌍 New timezone:", str),
}

# ── Helpers ─────────────────────────────────────────────────────────────────────

def _tz_now(tz_str: str) -> datetime:
    return datetime.now(pytz.timezone(tz_str))


def _date_str(dt: datetime) -> str:
    return dt.strftime("%d-%m-%Y")


def _display_date(dt: datetime) -> str:
    return dt.strftime("%A, %-d %b %Y")


def _bar_line(label: str, current: float, goal: float, unit: str = "", length: int = 10) -> str:
    bar = progress_bar(current, goal, length)
    pct = int((current / goal) * 100) if goal else 0
    return f"{label}: {current:.0f} / {goal:.0f}{unit}  {bar}  {pct}%"


# ── /start ──────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await sheets_service.get_profile()

    if not profile or not profile.get("name"):
        # First time: onboarding
        context.user_data["onboarding_step"] = 0
        context.user_data["onboarding_data"] = {}
        step_key, prompt = ONBOARDING_STEPS[0]
        await update.message.reply_text(
            "🎉 Welcome to DietTrackerBot!\n\n"
            "Let's set up your nutrition profile. You can change these anytime with /profile.\n\n"
            + prompt
        )
    else:
        # Returning user: show today summary
        tz_str = profile.get("timezone") or DEFAULT_TIMEZONE
        today = _date_str(_tz_now(tz_str))
        entries = await sheets_service.get_food_entries_for_date(today)
        water = await sheets_service.get_water_entries_for_date(today)
        total_cal = sum(e.calories for e in entries)
        total_water = sum(w.amount_ml for w in water)
        cal_goal = int(profile.get("daily_calorie_limit") or DEFAULT_CALORIE_LIMIT)
        water_goal = int(profile.get("water_goal_ml") or DEFAULT_WATER_GOAL_ML)
        name = profile.get("name", "")

        await update.message.reply_text(
            f"👋 Hey {name}! Here's your day so far:\n\n"
            + _bar_line("🔥 Calories", total_cal, cal_goal, " kcal") + "\n"
            + _bar_line("💧 Water", total_water, water_goal, " ml") + "\n\n"
            f"Send a food description or photo to log a meal!\n"
            f"Type /help to see all commands.",
            reply_markup=water_quick_keyboard(),
        )


async def handle_onboarding_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    step_idx = context.user_data.get("onboarding_step", 0)
    data = context.user_data.setdefault("onboarding_data", {})
    text = update.message.text.strip()

    if step_idx >= len(ONBOARDING_STEPS):
        return

    step_key, _ = ONBOARDING_STEPS[step_idx]
    data[step_key] = text

    step_idx += 1
    context.user_data["onboarding_step"] = step_idx

    if step_idx < len(ONBOARDING_STEPS):
        _, prompt = ONBOARDING_STEPS[step_idx]
        await update.message.reply_text(prompt)
    else:
        # All steps done — save profile
        profile = {
            "name":                data.get("name", ""),
            "daily_calorie_limit": data.get("daily_calorie_limit", str(DEFAULT_CALORIE_LIMIT)),
            "protein_goal_g":      data.get("protein_goal_g", str(DEFAULT_PROTEIN_GOAL)),
            "carbs_goal_g":        data.get("carbs_goal_g", str(DEFAULT_CARBS_GOAL)),
            "fat_goal_g":          data.get("fat_goal_g", str(DEFAULT_FAT_GOAL)),
            "fiber_goal_g":        data.get("fiber_goal_g", str(DEFAULT_FIBER_GOAL)),
            "water_goal_ml":       data.get("water_goal_ml", str(DEFAULT_WATER_GOAL_ML)),
            "timezone":            data.get("timezone", DEFAULT_TIMEZONE),
            "joined_date":         datetime.utcnow().strftime("%d-%m-%Y"),
        }
        await sheets_service.save_profile(profile)
        context.user_data.pop("onboarding_step", None)
        context.user_data.pop("onboarding_data", None)

        await update.message.reply_text(
            f"✅ Profile saved! Welcome, {profile['name']}! 🎉\n\n"
            "You're all set. Send me any food (text or photo) to start tracking!\n"
            "Use /help to see all available commands."
        )


# ── /help ───────────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 *DietTrackerBot Commands*\n\n"
        "🍽️ *Logging*\n"
        "  Just send any text or photo of food to log it!\n\n"
        "📊 *Reports*\n"
        "  /daily — Today's full nutrition report\n"
        "  /weekly — Last 7 days summary\n"
        "  /remaining — Quick snapshot of what's left today\n\n"
        "💧 *Water*\n"
        "  /water — Today's water log & progress\n\n"
        "⚙️ *Settings*\n"
        "  /profile — View & edit your goals\n"
        "  /edit — Edit last 5 food entries\n"
        "  /start — Welcome screen\n"
        "  /help — This message\n\n"
        "💡 _Tip: Send any food description like \"dal rice with ghee\" to log instantly!_",
        parse_mode="Markdown",
    )


# ── /daily ──────────────────────────────────────────────────────────────────────

async def cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await sheets_service.get_profile()
    tz_str = profile.get("timezone") or DEFAULT_TIMEZONE
    now = _tz_now(tz_str)
    date_str = _date_str(now)

    entries = await sheets_service.get_food_entries_for_date(date_str)
    water_entries = await sheets_service.get_water_entries_for_date(date_str)

    cal_goal   = int(profile.get("daily_calorie_limit") or DEFAULT_CALORIE_LIMIT)
    prot_goal  = float(profile.get("protein_goal_g")    or DEFAULT_PROTEIN_GOAL)
    carb_goal  = float(profile.get("carbs_goal_g")      or DEFAULT_CARBS_GOAL)
    fat_goal   = float(profile.get("fat_goal_g")        or DEFAULT_FAT_GOAL)
    fiber_goal = float(profile.get("fiber_goal_g")      or DEFAULT_FIBER_GOAL)
    water_goal = int(profile.get("water_goal_ml")       or DEFAULT_WATER_GOAL_ML)

    total_cal   = sum(e.calories  for e in entries)
    total_prot  = sum(e.protein_g for e in entries)
    total_carbs = sum(e.carbs_g   for e in entries)
    total_fat   = sum(e.fat_g     for e in entries)
    total_fiber = sum(e.fiber_g   for e in entries)
    total_water = sum(w.amount_ml for w in water_entries)

    # Meal list
    meal_icons = {"Breakfast": "🍳", "Lunch": "🍛", "Dinner": "🍽️", "Snack": "🍎"}
    meal_lines = ""
    for e in entries:
        icon = meal_icons.get(e.meal_type, "🍴")
        meal_lines += f"  {icon} {e.meal_type:<10} {e.food_name:<25} {e.calories} kcal\n"
    if not meal_lines:
        meal_lines = "  _No food logged yet today._\n"

    rem_cal   = max(0, cal_goal - total_cal)
    rem_prot  = max(0, prot_goal - total_prot)
    rem_water = max(0, water_goal - total_water)

    msg = (
        f"📅 *TODAY — {_display_date(now)}*\n"
        f"──────────────────────────────\n"
        f"{meal_lines}"
        f"──────────────────────────────\n"
        + _bar_line("🔥 Calories", total_cal, cal_goal, " kcal") + "\n"
        f"💪 Protein:  {total_prot:.1f} / {prot_goal:.0f} g\n"
        f"🌾 Carbs:    {total_carbs:.1f} / {carb_goal:.0f} g\n"
        f"🥑 Fat:      {total_fat:.1f} / {fat_goal:.0f} g\n"
        f"🌿 Fiber:    {total_fiber:.1f} / {fiber_goal:.0f} g\n"
        + _bar_line("💧 Water", total_water, water_goal, " ml") + "\n"
        f"──────────────────────────────\n"
        f"Remaining: {rem_cal} kcal · {rem_prot:.0f}g protein · {rem_water} ml water"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


# ── /weekly ─────────────────────────────────────────────────────────────────────

async def cmd_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await sheets_service.get_profile()
    tz_str = profile.get("timezone") or DEFAULT_TIMEZONE
    now = _tz_now(tz_str)

    dates = [_date_str(now - timedelta(days=i)) for i in range(6, -1, -1)]
    food_entries = await sheets_service.get_food_entries_for_range(dates)
    water_entries = await sheets_service.get_water_entries_for_range(dates)

    cal_goal   = int(profile.get("daily_calorie_limit") or DEFAULT_CALORIE_LIMIT)
    water_goal = int(profile.get("water_goal_ml")       or DEFAULT_WATER_GOAL_ML)

    food_by_date  = {d: [] for d in dates}
    water_by_date = {d: [] for d in dates}
    for e in food_entries:
        if e.date in food_by_date:
            food_by_date[e.date].append(e)
    for w in water_entries:
        if w.date in water_by_date:
            water_by_date[w.date].append(w)

    lines = ""
    total_cal_sum = 0
    total_water_sum = 0
    best_cal_day = ("", 0)
    best_water_day = ("", 0)
    streak = 0
    logged_days = 0

    for d in dates:
        dt = datetime.strptime(d, "%d-%m-%Y")
        day_name = dt.strftime("%a")
        day_cal = sum(e.calories for e in food_by_date[d])
        day_water = sum(w.amount_ml for w in water_by_date[d])
        bar = progress_bar(day_cal, cal_goal, 10)
        water_ok = "✅" if day_water >= water_goal else ("💧" if day_water >= water_goal * 0.7 else "⬇️")
        is_today = (d == _date_str(now))
        status = "🔄" if is_today else water_ok

        lines += f"  {day_name}  {bar}  {day_cal} kcal  💧 {day_water} ml  {status}\n"
        total_cal_sum   += day_cal
        total_water_sum += day_water

        if day_cal > best_cal_day[1]:
            best_cal_day = (day_name, day_cal)
        if day_water > best_water_day[1]:
            best_water_day = (day_name, day_water)
        if food_by_date[d]:
            logged_days += 1
            if d >= _date_str(now - timedelta(days=logged_days - 1)):
                streak = logged_days

    avg_cal   = total_cal_sum // 7
    avg_water = total_water_sum // 7

    # Save weekly summary
    await sheets_service.save_weekly_summary({
        "week_start":          dates[0],
        "total_calories":      total_cal_sum,
        "avg_calories_per_day": avg_cal,
        "total_protein":       sum(e.protein_g for e in food_entries),
        "total_carbs":         sum(e.carbs_g   for e in food_entries),
        "total_fat":           sum(e.fat_g     for e in food_entries),
        "total_water_ml":      total_water_sum,
        "avg_water_ml_per_day": avg_water,
        "streak_days":         streak,
    })

    msg = (
        f"📊 *WEEKLY REPORT — Last 7 Days*\n"
        f"──────────────────────────────\n"
        f"{lines}"
        f"──────────────────────────────\n"
        f"Avg: *{avg_cal} kcal/day*  |  Avg water: *{avg_water} ml/day*\n"
        f"Best calorie day: {best_cal_day[0]} ({best_cal_day[1]} kcal)\n"
        f"Best hydration day: {best_water_day[0]} ({best_water_day[1]} ml)\n"
        f"🔥 Streak: *{streak} days logged!*"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


# ── /water ──────────────────────────────────────────────────────────────────────

async def cmd_water(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await sheets_service.get_profile()
    tz_str = profile.get("timezone") or DEFAULT_TIMEZONE
    now = _tz_now(tz_str)
    date_str = _date_str(now)
    water_goal = int(profile.get("water_goal_ml") or DEFAULT_WATER_GOAL_ML)

    entries = await sheets_service.get_water_entries_for_date(date_str)
    total = sum(e.amount_ml for e in entries)
    bar = progress_bar(total, water_goal)
    pct = int((total / water_goal) * 100) if water_goal else 0
    remaining = max(0, water_goal - total)
    glasses_left = round(remaining / 250)

    lines = ""
    for w in entries:
        note = f"   ({w.note})" if w.note else ""
        lines += f"  {w.time}   {w.amount_ml} ml{note}\n"
    if not lines:
        lines = "  _No water logged yet today._\n"

    msg = (
        f"💧 *WATER TODAY — {_display_date(now)}*\n"
        f"─────────────────────────────────────\n"
        f"{lines}"
        f"─────────────────────────────────────\n"
        f"Total:     *{total} / {water_goal} ml*   {bar}   {pct}%\n"
        f"Remaining: *{remaining} ml*  (~{glasses_left} more glass{'es' if glasses_left != 1 else ''})"
    )
    await update.message.reply_text(
        msg, parse_mode="Markdown", reply_markup=water_quick_keyboard()
    )


# ── /remaining ──────────────────────────────────────────────────────────────────

async def cmd_remaining(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await sheets_service.get_profile()
    tz_str = profile.get("timezone") or DEFAULT_TIMEZONE
    now = _tz_now(tz_str)
    date_str = _date_str(now)

    entries = await sheets_service.get_food_entries_for_date(date_str)
    water_entries = await sheets_service.get_water_entries_for_date(date_str)

    cal_goal   = int(profile.get("daily_calorie_limit") or DEFAULT_CALORIE_LIMIT)
    prot_goal  = float(profile.get("protein_goal_g")    or DEFAULT_PROTEIN_GOAL)
    carb_goal  = float(profile.get("carbs_goal_g")      or DEFAULT_CARBS_GOAL)
    fat_goal   = float(profile.get("fat_goal_g")        or DEFAULT_FAT_GOAL)
    water_goal = int(profile.get("water_goal_ml")       or DEFAULT_WATER_GOAL_ML)

    total_cal   = sum(e.calories  for e in entries)
    total_prot  = sum(e.protein_g for e in entries)
    total_carbs = sum(e.carbs_g   for e in entries)
    total_fat   = sum(e.fat_g     for e in entries)
    total_water = sum(w.amount_ml for w in water_entries)

    rem_cal   = max(0, cal_goal - total_cal)
    rem_prot  = max(0, prot_goal - total_prot)
    rem_carbs = max(0, carb_goal - total_carbs)
    rem_fat   = max(0, fat_goal - total_fat)
    rem_water = max(0, water_goal - total_water)

    await update.message.reply_text(
        f"⚡ *Left today:* {rem_cal} kcal · {rem_prot:.0f}g protein · "
        f"{rem_carbs:.0f}g carbs · {rem_fat:.0f}g fat · 💧 {rem_water} ml water",
        parse_mode="Markdown",
    )


# ── /profile ────────────────────────────────────────────────────────────────────

async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    profile = await sheets_service.get_profile()
    if not profile:
        await update.message.reply_text("No profile found. Run /start to set one up.")
        return

    name      = profile.get("name", "")
    cal_goal  = profile.get("daily_calorie_limit", DEFAULT_CALORIE_LIMIT)
    prot_goal = profile.get("protein_goal_g",      DEFAULT_PROTEIN_GOAL)
    carb_goal = profile.get("carbs_goal_g",        DEFAULT_CARBS_GOAL)
    fat_goal  = profile.get("fat_goal_g",          DEFAULT_FAT_GOAL)
    fib_goal  = profile.get("fiber_goal_g",        DEFAULT_FIBER_GOAL)
    wat_goal  = profile.get("water_goal_ml",       DEFAULT_WATER_GOAL_ML)
    tz        = profile.get("timezone",            DEFAULT_TIMEZONE)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Edit Goals", callback_data="edit_profile")],
    ])
    await update.message.reply_text(
        f"👤 *YOUR PROFILE*\n"
        f"─────────────────────────\n"
        f"👋 Name:       {name}\n"
        f"🔥 Calories:   {cal_goal} kcal/day\n"
        f"💪 Protein:    {prot_goal} g/day\n"
        f"🌾 Carbs:      {carb_goal} g/day\n"
        f"🥑 Fat:        {fat_goal} g/day\n"
        f"🌿 Fiber:      {fib_goal} g/day\n"
        f"💧 Water:      {wat_goal} ml/day\n"
        f"🕐 Timezone:   {tz}\n"
        f"─────────────────────────",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def profile_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    buttons = [
        [InlineKeyboardButton("🔥 Calories",  callback_data="pf:daily_calorie_limit"),
         InlineKeyboardButton("💪 Protein",   callback_data="pf:protein_goal_g")],
        [InlineKeyboardButton("🌾 Carbs",     callback_data="pf:carbs_goal_g"),
         InlineKeyboardButton("🥑 Fat",       callback_data="pf:fat_goal_g")],
        [InlineKeyboardButton("🌿 Fiber",     callback_data="pf:fiber_goal_g"),
         InlineKeyboardButton("💧 Water",     callback_data="pf:water_goal_ml")],
        [InlineKeyboardButton("🌍 Timezone",  callback_data="pf:timezone")],
    ]
    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))


async def profile_field_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    field = query.data.split(":")[1]
    if field not in PROFILE_FIELDS:
        return

    prompt, _ = PROFILE_FIELDS[field]
    context.user_data["profile_edit_field"] = field
    await query.message.reply_text(prompt)


async def handle_profile_edit_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    field = context.user_data.get("profile_edit_field")
    if not field or field not in PROFILE_FIELDS:
        return

    _, cast = PROFILE_FIELDS[field]
    text = update.message.text.strip()
    try:
        value = cast(text)
    except ValueError:
        await update.message.reply_text("❌ Invalid value. Please try again:")
        return

    profile = await sheets_service.get_profile()
    profile[field] = str(value)
    await sheets_service.save_profile(profile)
    context.user_data.pop("profile_edit_field", None)

    await update.message.reply_text(f"✅ Updated! Use /profile to view your goals.")


# ── /edit ────────────────────────────────────────────────────────────────────────

async def cmd_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    entries = await sheets_service.get_last_food_entries(5)
    if not entries:
        await update.message.reply_text("No food entries found to edit.")
        return

    context.user_data["edit_entries"] = entries
    buttons = []
    lines = ""
    for i, e in enumerate(entries, start=1):
        lines += f"{i}. {e.meal_type} — {e.food_name} ({e.calories} kcal) [{e.date} {e.time}]\n"
        buttons.append([InlineKeyboardButton(f"✏️ Edit {i}", callback_data=f"edit_entry:{i-1}")])

    await update.message.reply_text(
        f"📝 *Last {len(entries)} entries:*\n\n{lines}\nTap to edit:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def edit_entry_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    idx = int(query.data.split(":")[1])
    entries = context.user_data.get("edit_entries", [])
    if idx >= len(entries):
        await query.message.reply_text("❌ Entry not found.")
        return

    entry = entries[idx]
    context.user_data["editing_entry"] = entry
    context.user_data["awaiting_edit_correction"] = True

    await query.message.reply_text(
        f"✏️ Editing: *{entry.food_name}* ({entry.calories} kcal)\n\n"
        "What should I change? (e.g. '1.5 cups instead of 1' or 'brown rice instead of white')",
        parse_mode="Markdown",
    )


async def handle_edit_correction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    entry: NutritionEntry = context.user_data.get("editing_entry")
    if not entry:
        return

    correction = update.message.text.strip()
    context.user_data.pop("awaiting_edit_correction", None)
    context.user_data.pop("editing_entry", None)

    processing_msg = await update.message.reply_text("🔍 Re-analysing…")

    try:
        result = await re_analyze_food(entry.food_name, correction)
    except Exception as e:
        await processing_msg.delete()
        await update.message.reply_text("❌ Could not re-analyse. Please try again.")
        return

    await processing_msg.delete()

    updated = NutritionEntry(
        date=entry.date,
        time=entry.time,
        meal_type=entry.meal_type,
        food_name=result.get("food_name", entry.food_name),
        calories=int(result.get("calories", entry.calories)),
        protein_g=float(result.get("protein_g", entry.protein_g)),
        carbs_g=float(result.get("carbs_g", entry.carbs_g)),
        fat_g=float(result.get("fat_g", entry.fat_g)),
        fiber_g=float(result.get("fiber_g", entry.fiber_g)),
        notes=result.get("notes", entry.notes),
        row_index=entry.row_index,
    )
    await sheets_service.update_food_entry(entry.row_index, updated)

    await update.message.reply_text(
        f"✅ Entry updated!\n\n"
        f"*{updated.food_name}*\n"
        f"🔥 {updated.calories} kcal  |  💪 {updated.protein_g:.1f}g  |  "
        f"🌾 {updated.carbs_g:.1f}g  |  🥑 {updated.fat_g:.1f}g",
        parse_mode="Markdown",
    )


# ── Inline callbacks router ─────────────────────────────────────────────────────

async def generic_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route generic inline callbacks."""
    query = update.callback_query
    data = query.data

    if data == "show_daily":
        await query.answer()
        await cmd_daily(update, context)
    elif data == "edit_last":
        await query.answer()
        await cmd_edit(update, context)
    elif data == "edit_profile":
        await profile_edit_callback(update, context)
    elif data.startswith("pf:"):
        await profile_field_callback(update, context)
    elif data.startswith("edit_entry:"):
        await edit_entry_callback(update, context)
    else:
        await query.answer()
