"""models/nutrition.py — NutritionEntry dataclass"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class NutritionEntry:
    date: str                        # DD-MM-YYYY
    time: str                        # HH:MM AM/PM
    meal_type: str                   # Breakfast / Lunch / Dinner / Snack
    food_name: str
    calories: int
    protein_g: float
    carbs_g: float
    fat_g: float
    fiber_g: float
    notes: Optional[str] = None
    row_index: Optional[int] = None  # Sheet row for editing

    def to_sheet_row(self) -> List:
        return [
            self.date,
            self.time,
            self.meal_type,
            self.food_name,
            self.calories,
            round(self.protein_g, 1),
            round(self.carbs_g, 1),
            round(self.fat_g, 1),
            round(self.fiber_g, 1),
            self.notes or "",
        ]

    @classmethod
    def from_sheet_row(cls, row: List, row_index: int) -> "NutritionEntry":
        def _f(v, default=0.0):
            try:
                return float(v)
            except (ValueError, TypeError):
                return default

        return cls(
            date=row[0] if len(row) > 0 else "",
            time=row[1] if len(row) > 1 else "",
            meal_type=row[2] if len(row) > 2 else "",
            food_name=row[3] if len(row) > 3 else "",
            calories=int(_f(row[4])) if len(row) > 4 else 0,
            protein_g=_f(row[5]) if len(row) > 5 else 0.0,
            carbs_g=_f(row[6]) if len(row) > 6 else 0.0,
            fat_g=_f(row[7]) if len(row) > 7 else 0.0,
            fiber_g=_f(row[8]) if len(row) > 8 else 0.0,
            notes=row[9] if len(row) > 9 else None,
            row_index=row_index,
        )
