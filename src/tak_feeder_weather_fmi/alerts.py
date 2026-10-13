# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Street3r
"""Weather alerts for single station observations.

Levels follow FMI's warning colours. The default thresholds are FMI's warning
criteria where FMI publishes numbers:

- Frost: temperature or wind chill, by region
  https://www.ilmatieteenlaitos.fi/tietoa-helle-ja-pakkasvaroitukset
- Heat: daily maximum temperature (here: the current temperature)
- Wind: land warnings by gusts, winter and summer values; sea warnings by
  10-minute mean wind  https://www.ilmatieteenlaitos.fi/tuulivaroitukset

Visibility and freezing precipitation have no numeric FMI warning criteria;
their defaults are this feeder's own choices.

FMI warnings are forecasts for areas. These alerts only say that one station
measures values at or beyond those levels right now.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Sequence

from .common import env, env_bool, env_float, env_list

YELLOW, ORANGE, RED = 1, 2, 3
LEVEL_NAMES = {YELLOW: "yellow", ORANGE: "orange", RED: "red"}
LEVEL_COLORS = {YELLOW: "-256", ORANGE: "-35072", RED: "-65536"}  # ARGB

# ATAK built-in icons (white, tinted with the level colour)
_DEFAULT_SET = "34ae1613-9645-4222-a9d2-e5f243dea2865"
ICONS = {
    "wind": f"{_DEFAULT_SET}/Transportation/Warning.png",
    "frost": f"{_DEFAULT_SET}/Weather/Snowflake.png",
    "freezing": f"{_DEFAULT_SET}/Weather/Icing.png",
    "heat": f"{_DEFAULT_SET}/Weather/sun.png",
    "visibility": f"{_DEFAULT_SET}/Transportation/Warning.png",
}
# When several alerts have the same level, the first in this list wins the icon
PRIORITY = ("wind", "frost", "freezing", "heat", "visibility")
CALLSIGN_WORDS = {"wind": "WIND", "frost": "FROST", "freezing": "ICE",
                  "heat": "HEAT", "visibility": "FOG"}

# WMO code table 4680 (wawa, automatic present weather): freezing drizzle and
# freezing rain, slight / moderate / heavy
FREEZING_CODES = {54: "freezing drizzle", 55: "freezing drizzle", 56: "freezing drizzle",
                  64: "freezing rain", 65: "freezing rain", 66: "freezing rain"}


def _levels(text: str, default: Sequence[float]) -> tuple[float, float, float]:
    """'a,b,c' -> (yellow, orange, red)."""
    parts = [p.strip() for p in text.split(",") if p.strip()]
    try:
        values = [float(p) for p in parts]
    except ValueError:
        values = []
    if len(values) != 3:
        values = list(default)
    return values[0], values[1], values[2]


@dataclass
class AlertSettings:
    """Thresholds as (yellow, orange, red). See .env.example."""

    min_level: int = ORANGE
    frost_south: tuple[float, float, float] = (-20, -30, -35)
    frost_central: tuple[float, float, float] = (-25, -35, -40)
    frost_north: tuple[float, float, float] = (-30, -40, -45)
    frost_central_lat: float = 62.5
    frost_north_lat: float = 65.5
    heat: tuple[float, float, float] = (27, 30, 35)
    gusts_winter: tuple[float, float, float] = (20, 25, 30)
    gusts_summer: tuple[float, float, float] = (15, 20, 25)
    mean_wind: tuple[float, float, float] = (14, 21, 33)
    visibility: tuple[float, float, float] = (1000, 200, 50)
    freezing: bool = True
    enabled: list[str] = field(default_factory=lambda: list(PRIORITY))

    @classmethod
    def from_env(cls) -> "AlertSettings":
        """Read thresholds."""
        d = cls()
        level = env("ALERT_MIN_LEVEL", "orange").lower()
        return cls(
            min_level={"yellow": YELLOW, "orange": ORANGE, "red": RED}.get(level, ORANGE),
            frost_south=_levels(env("FROST_SOUTH"), d.frost_south),
            frost_central=_levels(env("FROST_CENTRAL"), d.frost_central),
            frost_north=_levels(env("FROST_NORTH"), d.frost_north),
            frost_central_lat=env_float("FROST_CENTRAL_LAT", d.frost_central_lat),
            frost_north_lat=env_float("FROST_NORTH_LAT", d.frost_north_lat),
            heat=_levels(env("HEAT"), d.heat),
            gusts_winter=_levels(env("WIND_GUSTS_WINTER"), d.gusts_winter),
            gusts_summer=_levels(env("WIND_GUSTS_SUMMER"), d.gusts_summer),
            mean_wind=_levels(env("WIND_MEAN"), d.mean_wind),
            visibility=_levels(env("VISIBILITY"), d.visibility),
            freezing=env_bool("FREEZING", d.freezing),
            enabled=[a.lower() for a in env_list("ALERTS", ",".join(PRIORITY))],
        )


@dataclass
class Alert:
    """One alert condition."""

    kind: str
    level: int
    text: str

    @property
    def color(self) -> str:
        """ARGB colour of the level."""
        return LEVEL_COLORS[self.level]

    @property
    def icon(self) -> str:
        """ATAK iconset path."""
        return ICONS[self.kind]


def wind_chill(temp: Optional[float], wind: Optional[float]) -> Optional[float]:
    """Wind chill (°C) with wind in m/s at 10 m, the JAG/TI index in the form
    FMI uses for its frost warnings. Defined for T <= 10 °C and wind >= 1.3 m/s."""
    if temp is None or wind is None or temp > 10 or wind < 1.3:
        return None
    v = wind ** 0.16
    return 13.12 + 0.6215 * temp - 13.956 * v + 0.4867 * temp * v


def _below(value: float, levels: Sequence[float]) -> int:
    return max((lvl for lvl, limit in zip((YELLOW, ORANGE, RED), levels) if value <= limit),
               default=0)


def _above(value: float, levels: Sequence[float]) -> int:
    return max((lvl for lvl, limit in zip((YELLOW, ORANGE, RED), levels) if value >= limit),
               default=0)


def evaluate(values: dict[str, float], lat: float, when: datetime,
             s: AlertSettings) -> list[Alert]:
    """Alerts at or above the minimum level, most severe first."""
    v = values.get
    found: list[Alert] = []
    temp, mean, gust = v("t2m"), v("ws_10min"), v("wg_10min")

    if "frost" in s.enabled and temp is not None:
        if lat >= s.frost_north_lat:
            region, levels = "northern", s.frost_north
        elif lat >= s.frost_central_lat:
            region, levels = "central", s.frost_central
        else:
            region, levels = "southern", s.frost_south
        chill = wind_chill(temp, mean)
        felt = min(temp, chill) if chill is not None else temp
        level = _below(felt, levels)
        if level:
            what = (f"wind chill {chill:.0f} °C" if chill is not None and chill < temp
                    else f"temperature {temp:.1f} °C")
            found.append(Alert("frost", level, f"Frost: {what} "
                               f"(≤ {levels[level - 1]:g} °C, {region} Finland)"))

    if "heat" in s.enabled and temp is not None:
        level = _above(temp, s.heat)
        if level:
            found.append(Alert("heat", level,
                               f"Heat: {temp:.1f} °C (≥ {s.heat[level - 1]:g} °C)"))

    if "wind" in s.enabled:
        winter = when.month >= 11 or when.month <= 4
        gust_levels = s.gusts_winter if winter else s.gusts_summer
        g_level = _above(gust, gust_levels) if gust is not None else 0
        m_level = _above(mean, s.mean_wind) if mean is not None else 0
        if g_level >= m_level and g_level:
            found.append(Alert("wind", g_level, f"Wind: gusts {gust:.1f} m/s "
                               f"(≥ {gust_levels[g_level - 1]:g} m/s)"))
        elif m_level:
            found.append(Alert("wind", m_level, f"Wind: mean {mean:.1f} m/s "
                               f"(≥ {s.mean_wind[m_level - 1]:g} m/s)"))

    vis = v("vis")
    if "visibility" in s.enabled and vis is not None:
        level = _below(vis, s.visibility)
        if level:
            found.append(Alert("visibility", level, f"Visibility: {vis:.0f} m "
                               f"(≤ {s.visibility[level - 1]:g} m)"))

    wawa = v("wawa")
    if "freezing" in s.enabled and s.freezing and wawa is not None:
        name = FREEZING_CODES.get(int(wawa))
        if name:
            found.append(Alert("freezing", ORANGE, f"Icing: {name} (present weather {int(wawa)})"))

    found = [a for a in found if a.level >= s.min_level]
    found.sort(key=lambda a: (-a.level, PRIORITY.index(a.kind)))
    return found
