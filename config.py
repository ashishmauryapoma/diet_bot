"""
config.py — Central configuration loaded from .env
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ───────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]

# ── Gemini ─────────────────────────────────────────────────────────────────────
GEMINI_API_KEY: str = os.environ["GEMINI_API_KEY"]
GEMINI_MODEL: str = "gemini-1.5-flash"
GEMINI_MAX_CALLS_PER_MINUTE: int = 5

# ── Google Sheets ──────────────────────────────────────────────────────────────
GOOGLE_SHEETS_CREDENTIALS_JSON: str = os.environ["GOOGLE_SHEETS_CREDENTIALS_JSON"]
SPREADSHEET_ID: str = os.environ["SPREADSHEET_ID"]

# Sheet names
SHEET_FOOD_LOG      = "Food Log"
SHEET_WATER_LOG     = "Water Log"
SHEET_USER_PROFILE  = "User Profile"
SHEET_WEEKLY_SUMMARY = "Weekly Summary"

# ── Auth ───────────────────────────────────────────────────────────────────────
AUTHORIZED_USER_ID: str | None = os.getenv("AUTHORIZED_USER_ID") or None
BOT_PASSWORD_HASH: str | None  = os.getenv("BOT_PASSWORD_HASH") or None
MAX_FAILED_ATTEMPTS: int = 3
LOCKOUT_MINUTES: int     = 30

# ── Nutrition defaults ─────────────────────────────────────────────────────────
DEFAULT_CALORIE_LIMIT: int = int(os.getenv("DEFAULT_CALORIE_LIMIT", 2000))
DEFAULT_PROTEIN_GOAL:  int = int(os.getenv("DEFAULT_PROTEIN_GOAL",  120))
DEFAULT_CARBS_GOAL:    int = int(os.getenv("DEFAULT_CARBS_GOAL",    250))
DEFAULT_FAT_GOAL:      int = int(os.getenv("DEFAULT_FAT_GOAL",      65))
DEFAULT_FIBER_GOAL:    int = int(os.getenv("DEFAULT_FIBER_GOAL",    30))
DEFAULT_WATER_GOAL_ML: int = int(os.getenv("DEFAULT_WATER_GOAL_ML", 2500))
DEFAULT_TIMEZONE:      str = os.getenv("DEFAULT_TIMEZONE", "Asia/Kolkata")

# ── Meal type by hour (local time) ─────────────────────────────────────────────
MEAL_HOUR_MAP = {
    range(5,  11): "Breakfast",
    range(11, 16): "Lunch",
    range(16, 19): "Snack",
    range(19, 24): "Dinner",
    range(0,   5): "Dinner",   # late night
}

def get_default_meal_type(hour: int) -> str:
    for hour_range, meal in MEAL_HOUR_MAP.items():
        if hour in hour_range:
            return meal
    return "Snack"

# ── Progress bar helper ────────────────────────────────────────────────────────
def progress_bar(current: float, total: float, length: int = 10) -> str:
    if total <= 0:
        return "░" * length
    filled = min(int((current / total) * length), length)
    return "█" * filled + "░" * (length - filled)
