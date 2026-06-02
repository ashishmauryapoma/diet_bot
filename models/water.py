"""models/water.py — WaterEntry dataclass"""
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class WaterEntry:
    date: str        # DD-MM-YYYY
    time: str        # HH:MM AM/PM
    amount_ml: int
    note: Optional[str] = None
    row_index: Optional[int] = None

    def to_sheet_row(self) -> List:
        return [
            self.date,
            self.time,
            self.amount_ml,
            self.note or "",
        ]

    @classmethod
    def from_sheet_row(cls, row: List, row_index: int) -> "WaterEntry":
        return cls(
            date=row[0] if len(row) > 0 else "",
            time=row[1] if len(row) > 1 else "",
            amount_ml=int(float(row[2])) if len(row) > 2 else 0,
            note=row[3] if len(row) > 3 else None,
            row_index=row_index,
        )
