# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright Stefan Gofferje (tak-feeder-weather-fmi)
# Copyright (C) 2026 Street3r (changes, see README)
"""FMI weather observations (and optionally STUK radiation) -> CoT."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from . import __version__, alerts, fmi
from .common import Health, cot_event, env, env_bool, env_float, fetch, remarks_text

LOG = logging.getLogger("tak_feeder_weather_fmi")

WEATHER_QUERY = "fmi::observations::weather::multipointcoverage"
RADIATION_QUERY = "stuk::observations::external-radiation::latest::multipointcoverage"
DEFAULT_PARAMETERS = "t2m,ws_10min,wg_10min,wd_10min,rh,p_sea,vis,snow_aws,wawa"
WEATHER_TYPE = "b-w-A-t"  # as in upstream
RADIATION_TYPE = "a-n-G"
# Alert look: ATAK built-in FEMA HAZMAT icon. Normal stations keep the plain
# marker; only an alert changes the icon.
RADIATION_ALERT_ICON = "f8f7f666-8b28-4b57-9fbb-e48e61d33b79/Iron Sites/HAZMAT.png"


@dataclass
class Settings:
    """Feeder settings from the environment (see .env.example)."""

    bbox: str = "18,59,33,71"
    parameters: str = DEFAULT_PARAMETERS
    update_interval: float = 300.0
    stale_seconds: float = 900.0
    history_minutes: int = 60
    weather: bool = True
    radiation: bool = False
    radiation_alert: float = 0.4
    radiation_alert_icon: str = RADIATION_ALERT_ICON
    radiation_alert_color: str = ""
    radiation_alert_type: str = RADIATION_TYPE
    timezone: str = "Europe/Helsinki"
    source_line: str = "Source: Finnish Meteorological Institute open data, CC BY 4.0"
    radiation_source_line: str = "Source: STUK / FMI open data, CC BY 4.0"
    alerts: alerts.AlertSettings = field(default_factory=alerts.AlertSettings)

    @classmethod
    def from_env(cls) -> "Settings":
        """Read settings."""
        d = cls()
        return cls(
            bbox=env("FMI_BBOX", d.bbox),
            parameters=env("WEATHER_PARAMETERS", d.parameters),
            update_interval=env_float("UPDATE_INTERVAL", d.update_interval),
            stale_seconds=env_float("STALE_SECONDS", d.stale_seconds),
            history_minutes=int(env_float("HISTORY_MINUTES", d.history_minutes)),
            weather=env_bool("WEATHER", d.weather),
            radiation=env_bool("RADIATION", d.radiation),
            radiation_alert=env_float("RADIATION_ALERT_LEVEL", d.radiation_alert),
            radiation_alert_icon=env("RADIATION_ALERT_ICON", d.radiation_alert_icon),
            radiation_alert_color=env("RADIATION_ALERT_COLOR", d.radiation_alert_color),
            radiation_alert_type=env("RADIATION_ALERT_TYPE", d.radiation_alert_type),
            timezone=env("TIMEZONE", d.timezone),
            source_line=env("SOURCE_LINE", d.source_line),
            radiation_source_line=env("RADIATION_SOURCE_LINE", d.radiation_source_line),
            alerts=alerts.AlertSettings.from_env(),
        )


def _fmt(value: Optional[float], unit: str, digits: int = 1) -> Optional[str]:
    return None if value is None else f"{value:.{digits}f} {unit}"


def weather_lines(values: dict[str, float]) -> list[Optional[str]]:
    """Human-readable lines for the observed values that exist."""
    v = values.get
    wind = None
    if v("ws_10min") is not None:
        wind = f"Wind: {v('ws_10min'):.1f} m/s"
        if v("wg_10min") is not None:
            wind += f", gusts {v('wg_10min'):.1f} m/s"
        if v("wd_10min") is not None:
            wind += f", from {v('wd_10min'):.0f}°"
    snow = v("snow_aws")
    vis = v("vis")
    chill = alerts.wind_chill(v("t2m"), v("ws_10min"))
    return [
        f"Temperature: {v('t2m'):.1f} °C" if v("t2m") is not None else None,
        f"Wind chill: {chill:.0f} °C" if chill is not None else None,
        wind,
        f"Humidity: {v('rh'):.0f} %" if v("rh") is not None else None,
        f"Pressure: {v('p_sea'):.1f} hPa" if v("p_sea") is not None else None,
        f"Visibility: {vis / 1000:.1f} km" if vis is not None else None,
        f"Snow depth: {snow:.0f} cm" if snow is not None and snow >= 0 else None,
    ]


class WeatherSource:
    """Fetches observations every round and builds CoT."""

    def __init__(self, settings: Settings, health: Optional[Health] = None) -> None:
        self.s = settings
        self.health = health
        self.tz = ZoneInfo(settings.timezone)
        self.headers = {"User-Agent": f"tak-feeder-weather-fmi/{__version__}"}

    def _local(self, when: Optional[datetime]) -> Optional[str]:
        return when.astimezone(self.tz).strftime("%H:%M") if when else None

    def weather_event(self, st: fmi.Station, now: datetime) -> bytes:
        """CoT for one weather station. A normal station is a plain marker; an
        alert changes the icon (tinted with the level colour) and callsign."""
        found = alerts.evaluate(st.values, st.lat, st.time or now, self.s.alerts)
        lines: list[Optional[str]] = [
            f"ALERT ({alerts.LEVEL_NAMES[a.level]}) {a.text}" for a in found]
        lines += [f"Station: {st.name}", *weather_lines(st.values)]
        lines.append(f"Observed: {self._local(st.time)}" if st.time else None)
        tags = ["weather", "FMI"]
        callsign = f"FMI {st.name}"
        top = found[0] if found else None
        if top:
            tags += ["alert"] + [a.kind for a in found]
            callsign += f" {alerts.CALLSIGN_WORDS[top.kind]}"
        return cot_event(
            uid=f"fmisensor.{st.fmisid}",  # as in upstream
            cot_type=WEATHER_TYPE,
            lat=st.lat, lon=st.lon,
            callsign=callsign,
            remarks=remarks_text(lines, self.s.source_line, tags),
            stale=self.s.stale_seconds,
            how="m-c",
            icon=top.icon if top else "",
            color=top.color if top else "",
            now=now,
        )

    def radiation_event(self, st: fmi.Station, now: datetime) -> Optional[bytes]:
        """CoT for one radiation station (dose rate in µSv/h)."""
        dose = st.values.get("DR_PT10M_avg", st.values.get("DRS1_PT10M_avg"))
        if dose is None:
            return None
        alert = dose >= self.s.radiation_alert
        lines = [
            f"Station: {st.name}",
            f"Dose rate: {dose:.3f} µSv/h (10 min average)",
            f"ABOVE ALERT LEVEL {self.s.radiation_alert:g} µSv/h" if alert else None,
            f"Observed: {self._local(st.time)}" if st.time else None,
        ]
        tags = ["radiation", "STUK"] + (["alert"] if alert else [])
        return cot_event(
            uid=f"fmiradiation.{st.fmisid}",
            cot_type=self.s.radiation_alert_type if alert else RADIATION_TYPE,
            lat=st.lat, lon=st.lon,
            callsign=f"STUK {st.name}" + (" ALERT" if alert else ""),
            remarks=remarks_text(lines, self.s.radiation_source_line, tags),
            stale=self.s.stale_seconds,
            how="m-c",
            icon=self.s.radiation_alert_icon if alert else "",
            color=self.s.radiation_alert_color if alert else "",
            now=now,
        )

    async def _get(self, url: str) -> Optional[bytes]:
        try:
            return await fetch(url, self.headers, timeout=60)
        except Exception as exc:  # noqa: BLE001  pylint: disable=broad-exception-caught
            LOG.warning("FMI request failed: %s", exc)
            return None

    async def events(self) -> list[bytes]:
        """Source protocol: fetch and convert."""
        now = datetime.now(timezone.utc)
        started = time.monotonic()
        events: list[bytes] = []
        failed = False
        stations: list[fmi.Station] = []

        if self.s.weather:
            url = fmi.query_url(WEATHER_QUERY, now, self.s.history_minutes,
                                bbox=self.s.bbox, parameters=self.s.parameters, timestep="10")
            body = await self._get(url)
            try:
                stations = fmi.parse(body) if body else []
            except Exception as exc:  # noqa: BLE001  pylint: disable=broad-exception-caught
                LOG.warning("FMI weather response not understood: %s", exc)
                stations = []
            failed = body is None or not stations
            events += [self.weather_event(st, now) for st in stations]

        rad_count = 0
        if self.s.radiation:
            body = await self._get(fmi.query_url(RADIATION_QUERY, now, 0, bbox=self.s.bbox))
            rad_failed = body is None
            try:
                for st in fmi.parse(body) if body else []:
                    event = self.radiation_event(st, now)
                    if event:
                        events.append(event)
                        rad_count += 1
            except Exception as exc:  # noqa: BLE001  pylint: disable=broad-exception-caught
                LOG.warning("STUK radiation response not understood: %s", exc)
                rad_failed = True
            # Radiation only counts for health when it is all this feeder does
            if not self.s.weather:
                failed = rad_failed or not rad_count

        LOG.info("Weather: %d stations, radiation: %d (%.1f s)",
                 len(stations), rad_count, time.monotonic() - started)
        if self.health is not None:
            if failed:
                self.health.error()
            else:
                self.health.ok()
        return events

    async def start(self) -> None:
        """Nothing runs in the background."""
