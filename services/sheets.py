"""
services/sheets.py — Google Sheets read/write with retry logic
"""
import asyncio
import logging
from datetime import datetime, date
from typing import List, Optional, Dict, Any
import time

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from gspread.exceptions import APIError

import config
from models.nutrition import NutritionEntry
from models.water import WaterEntry

logger = logging.getLogger(__name__)

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

FOOD_LOG_HEADERS = [
    "date", "time", "meal_type", "food_name",
    "calories", "protein_g", "carbs_g", "fat_g", "fiber_g", "notes"
]
WATER_LOG_HEADERS = ["date", "time", "amount_ml", "note"]
PROFILE_HEADERS = [
    "daily_calorie_limit", "protein_goal_g", "carbs_goal_g",
    "fat_goal_g", "fiber_goal_g", "water_goal_ml",
    "timezone", "joined_date", "name"
]
WEEKLY_HEADERS = [
    "week_start", "total_calories", "avg_calories_per_day",
    "total_protein", "total_carbs", "total_fat",
    "total_water_ml", "avg_water_ml_per_day", "streak_days"
]


class SheetsService:
    """Thread-safe (asyncio-friendly) Google Sheets client with retry."""

    def __init__(self):
        self._client: Optional[gspread.Client] = None
        self._spreadsheet: Optional[gspread.Spreadsheet] = None
        self._last_connect: float = 0

    # ── Connection ─────────────────────────────────────────────────────────────

    def _connect(self) -> None:
        creds = ServiceAccountCredentials.from_json_keyfile_name(
            config.GOOGLE_SHEETS_CREDENTIALS_JSON, SCOPES
        )
        self._client = gspread.authorize(creds)
        self._spreadsheet = self._client.open_by_key(config.SPREADSHEET_ID)
        self._last_connect = time.time()
        logger.info("Connected to Google Sheets.")

    def _get_spreadsheet(self) -> gspread.Spreadsheet:
        if self._spreadsheet is None or time.time() - self._last_connect > 3000:
            self._connect()
        return self._spreadsheet

    def _sheet(self, name: str) -> gspread.Worksheet:
        ss = self._get_spreadsheet()
        try:
            ws = ss.worksheet(name)
        except gspread.WorksheetNotFound:
            ws = ss.add_worksheet(title=name, rows=1000, cols=20)
            self._init_headers(ws, name)
        return ws

    def _init_headers(self, ws: gspread.Worksheet, name: str) -> None:
        header_map = {
            config.SHEET_FOOD_LOG: FOOD_LOG_HEADERS,
            config.SHEET_WATER_LOG: WATER_LOG_HEADERS,
            config.SHEET_USER_PROFILE: PROFILE_HEADERS,
            config.SHEET_WEEKLY_SUMMARY: WEEKLY_HEADERS,
        }
        headers = header_map.get(name)
        if headers:
            ws.append_row(headers)

    def _retry(self, fn, *args, retries=3, **kwargs):
        """Exponential backoff retry wrapper."""
        for attempt in range(retries):
            try:
                return fn(*args, **kwargs)
            except APIError as e:
                if attempt == retries - 1:
                    raise
                wait = 2 ** attempt
                logger.warning(f"Sheets API error (attempt {attempt+1}): {e}. Retrying in {wait}s…")
                time.sleep(wait)
                self._connect()  # reconnect on error

    # ── Food Log ───────────────────────────────────────────────────────────────

    async def append_food_entry(self, entry: NutritionEntry) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_append_food, entry)

    def _sync_append_food(self, entry: NutritionEntry) -> None:
        ws = self._sheet(config.SHEET_FOOD_LOG)
        self._retry(ws.append_row, entry.to_sheet_row())

    async def get_food_entries_for_date(self, target_date: str) -> List[NutritionEntry]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_food_for_date, target_date)

    def _sync_food_for_date(self, target_date: str) -> List[NutritionEntry]:
        ws = self._sheet(config.SHEET_FOOD_LOG)
        rows = self._retry(ws.get_all_values)
        entries = []
        for i, row in enumerate(rows[1:], start=2):  # skip header
            if row and row[0] == target_date:
                entries.append(NutritionEntry.from_sheet_row(row, i))
        return entries

    async def get_food_entries_for_range(self, dates: List[str]) -> List[NutritionEntry]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_food_for_range, dates)

    def _sync_food_for_range(self, dates: List[str]) -> List[NutritionEntry]:
        ws = self._sheet(config.SHEET_FOOD_LOG)
        rows = self._retry(ws.get_all_values)
        date_set = set(dates)
        entries = []
        for i, row in enumerate(rows[1:], start=2):
            if row and row[0] in date_set:
                entries.append(NutritionEntry.from_sheet_row(row, i))
        return entries

    async def get_last_food_entries(self, n: int = 5) -> List[NutritionEntry]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_last_food, n)

    def _sync_last_food(self, n: int) -> List[NutritionEntry]:
        ws = self._sheet(config.SHEET_FOOD_LOG)
        rows = self._retry(ws.get_all_values)
        data_rows = rows[1:]  # skip header
        results = []
        for i, row in enumerate(reversed(data_rows), start=1):
            if row and any(row):
                orig_idx = len(data_rows) - i + 2  # 1-based sheet row
                results.append(NutritionEntry.from_sheet_row(row, orig_idx))
            if len(results) >= n:
                break
        return results

    async def update_food_entry(self, row_index: int, entry: NutritionEntry) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_update_food, row_index, entry)

    def _sync_update_food(self, row_index: int, entry: NutritionEntry) -> None:
        ws = self._sheet(config.SHEET_FOOD_LOG)
        row_data = entry.to_sheet_row()
        for col_idx, value in enumerate(row_data, start=1):
            self._retry(ws.update_cell, row_index, col_idx, value)

    # ── Water Log ──────────────────────────────────────────────────────────────

    async def append_water_entry(self, entry: WaterEntry) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_append_water, entry)

    def _sync_append_water(self, entry: WaterEntry) -> None:
        ws = self._sheet(config.SHEET_WATER_LOG)
        self._retry(ws.append_row, entry.to_sheet_row())

    async def get_water_entries_for_date(self, target_date: str) -> List[WaterEntry]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_water_for_date, target_date)

    def _sync_water_for_date(self, target_date: str) -> List[WaterEntry]:
        ws = self._sheet(config.SHEET_WATER_LOG)
        rows = self._retry(ws.get_all_values)
        entries = []
        for i, row in enumerate(rows[1:], start=2):
            if row and row[0] == target_date:
                entries.append(WaterEntry.from_sheet_row(row, i))
        return entries

    async def get_water_entries_for_range(self, dates: List[str]) -> List[WaterEntry]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_water_for_range, dates)

    def _sync_water_for_range(self, dates: List[str]) -> List[WaterEntry]:
        ws = self._sheet(config.SHEET_WATER_LOG)
        rows = self._retry(ws.get_all_values)
        date_set = set(dates)
        entries = []
        for i, row in enumerate(rows[1:], start=2):
            if row and row[0] in date_set:
                entries.append(WaterEntry.from_sheet_row(row, i))
        return entries

    # ── User Profile ───────────────────────────────────────────────────────────

    async def get_profile(self) -> Dict[str, Any]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_get_profile)

    def _sync_get_profile(self) -> Dict[str, Any]:
        ws = self._sheet(config.SHEET_USER_PROFILE)
        rows = self._retry(ws.get_all_values)
        if len(rows) < 2 or not any(rows[1]):
            return {}
        row = rows[1]
        keys = PROFILE_HEADERS
        return {k: (row[i] if i < len(row) else "") for i, k in enumerate(keys)}

    async def save_profile(self, profile: Dict[str, Any]) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_save_profile, profile)

    def _sync_save_profile(self, profile: Dict[str, Any]) -> None:
        ws = self._sheet(config.SHEET_USER_PROFILE)
        rows = self._retry(ws.get_all_values)
        row_data = [str(profile.get(k, "")) for k in PROFILE_HEADERS]
        if len(rows) < 2:
            self._retry(ws.append_row, row_data)
        else:
            for col_idx, value in enumerate(row_data, start=1):
                self._retry(ws.update_cell, 2, col_idx, value)

    # ── Weekly Summary ─────────────────────────────────────────────────────────

    async def save_weekly_summary(self, data: Dict[str, Any]) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_save_weekly, data)

    def _sync_save_weekly(self, data: Dict[str, Any]) -> None:
        ws = self._sheet(config.SHEET_WEEKLY_SUMMARY)
        row_data = [str(data.get(k, "")) for k in WEEKLY_HEADERS]
        self._retry(ws.append_row, row_data)


# Singleton
sheets_service = SheetsService()
