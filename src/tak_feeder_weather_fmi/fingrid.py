# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Street3r
"""Fingrid open data: real-time state of the Finnish power system (dataset 209)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

LATEST_URL = "https://data.fingrid.fi/api/datasets/209/data/latest"

# Fingrid's colour codes for the power system state
GREEN, YELLOW, RED, BLACK, BLUE = 1, 2, 3, 4, 5
STATES = {
    GREEN: ("green", "normal"),
    YELLOW: ("yellow", "endangered"),
    RED: ("red", "serious: load shedding or high risk of a major disturbance"),
    BLACK: ("black", "blackout in a large area or the whole country"),
    BLUE: ("blue", "restoring the power system after a disturbance"),
}
COLORS = {YELLOW: "-256", RED: "-65536", BLACK: "-16777216", BLUE: "-16776961"}  # ARGB


@dataclass
class State:
    value: int
    since: Optional[datetime]


def _time(text: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None
    except ValueError:
        return None


def parse(body: bytes) -> State:
    """{"value": 1, "startTime": "..."} -> State. Raises ValueError if unknown."""
    data = json.loads(body)
    value = int(data["value"])
    if value not in STATES:
        raise ValueError(f"unknown power system state {value}")
    return State(value, _time(data.get("startTime", "")))
