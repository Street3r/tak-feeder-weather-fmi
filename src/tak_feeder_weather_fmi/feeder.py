# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright Stefan Gofferje (tak-feeder-weather-fmi)
# Copyright (C) 2026 Street3r (changes, see README)
"""FMI weather observations (and optionally STUK radiation and the Fingrid
power system state) -> CoT."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from . import __version__, alerts, fingrid, fmi
from .common import Health, SendPolicy, cot_event, env, env_bool, env_float, fetch, remarks_text

LOG = logging.getLogger("tak_feeder_weather_fmi")

WEATHER_QUERY = "fmi::observations::weather::multipointcoverage"
RADIATION_QUERY = "stuk::observations::external-radiation::latest::multipointcoverage"
DEFAULT_PARAMETERS = "t2m,ws_10min,wg_10min,wd_10min,rh,p_sea,vis,snow_aws,wawa"
WEATHER_TYPE = "b-w-A-t"  # as in upstream
# Normal radiation stations look like weather stations; the alert type stays
# a neutral ground point under the alert icon.
RADIATION_TYPE = WEATHER_TYPE
RADIATION_ALERT_TYPE = "a-n-G"
# Alert look: ATAK built-in FEMA HAZMAT icon. Normal stations keep the plain
# marker; only an alert changes the icon.
RADIATION_ALERT_ICON = "f8f7f666-8b28-4b57-9fbb-e48e61d33b79/Iron Sites/HAZMAT.png"
# FMI observations come in 10-minute steps; faster requests only use up the
# shared FMI quota (20,000 requests a day).
MIN_FETCH_INTERVAL = 300.0
# Fingrid publishes the power system state every 3 minutes
MIN_FINGRID_INTERVAL = 60.0
FINGRID_TYPE = "a-n-G"
FINGRID_ICON = f"{alerts._DEFAULT_SET}/Transportation/Warning.png"  # pylint: disable=protected-access


def fetch_interval(value: float) -> float:
    """FETCH_INTERVAL, raised to MIN_FETCH_INTERVAL."""
    if value < MIN_FETCH_INTERVAL:
        LOG.warning("FETCH_INTERVAL=%g is below the minimum, using %g", value, MIN_FETCH_INTERVAL)
        return MIN_FETCH_INTERVAL
    return value


@dataclass
class Settings:
    """Feeder settings from the environment (see .env.example)."""

    bbox: str = "18,59,33,71"
    parameters: str = DEFAULT_PARAMETERS
    send: SendPolicy = field(default_factory=SendPolicy)
    fetch_interval: float = 300.0
    history_minutes: int = 60
    weather: bool = True
    radiation: bool = False
    only_alerts: bool = False
    radiation_alert: float = 0.4
    radiation_alert_icon: str = RADIATION_ALERT_ICON
    radiation_alert_color: str = ""
    radiation_alert_type: str = RADIATION_ALERT_TYPE
    fingrid_key: str = ""
    fingrid_alert_level: int = fingrid.RED
    fingrid_interval: float = 180.0
    # One national marker; default near the geographic centre of Finland
    fingrid_lat: float = 64.0
    fingrid_lon: float = 26.0
    timezone: str = "Europe/Helsinki"
    source_line: str = "Source: Finnish Meteorological Institute open data, CC BY 4.0"
    radiation_source_line: str = "Source: STUK / FMI open data, CC BY 4.0"
    fingrid_source_line: str = "Source: Fingrid open data, CC BY 4.0"
    alerts: alerts.AlertSettings = field(default_factory=alerts.AlertSettings)

    @classmethod
    def from_env(cls) -> "Settings":
        """Read settings."""
        d = cls()
        return cls(
            bbox=env("FMI_BBOX", d.bbox),
            parameters=env("WEATHER_PARAMETERS", d.parameters),
            send=SendPolicy.from_env(moving_aliases=(), stationary_aliases=(),
                                     static_aliases=("UPDATE_INTERVAL",)),
            fetch_interval=fetch_interval(env_float("FETCH_INTERVAL", d.fetch_interval)),
            history_minutes=int(env_float("HISTORY_MINUTES", d.history_minutes)),
            weather=env_bool("WEATHER", d.weather),
            radiation=env_bool("RADIATION", d.radiation),
            only_alerts=env_bool("ONLY_ALERTS", d.only_alerts),
            radiation_alert=env_float("RADIATION_ALERT_LEVEL", d.radiation_alert),
            radiation_alert_icon=env("RADIATION_ALERT_ICON", d.radiation_alert_icon),
            radiation_alert_color=env("RADIATION_ALERT_COLOR", d.radiation_alert_color),
            radiation_alert_type=env("RADIATION_ALERT_TYPE", d.radiation_alert_type),
            fingrid_key=env("FINGRID_API_KEY", d.fingrid_key),
            fingrid_alert_level=int(env_float("FINGRID_ALERT_LEVEL", d.fingrid_alert_level)),
            fingrid_interval=max(MIN_FINGRID_INTERVAL,
                                 env_float("FINGRID_INTERVAL", d.fingrid_interval)),
            fingrid_lat=env_float("FINGRID_LAT", d.fingrid_lat),
            fingrid_lon=env_float("FINGRID_LON", d.fingrid_lon),
            timezone=env("TIMEZONE", d.timezone),
            source_line=env("SOURCE_LINE", d.source_line),
            radiation_source_line=env("RADIATION_SOURCE_LINE", d.radiation_source_line),
            fingrid_source_line=env("FINGRID_SOURCE_LINE", d.fingrid_source_line),
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
    """Fetches observations every FETCH_INTERVAL and sends the latest ones
    every round with a fresh time, so a client that connects sees every
    station within one round. A failed fetch is retried the next round; the
    last good data is sent for at most CACHE_ROUNDS fetch intervals."""

    CACHE_ROUNDS = 3

    def __init__(self, settings: Settings, health: Optional[Health] = None) -> None:
        self.s = settings
        self.health = health
        self.tz = ZoneInfo(settings.timezone)
        self.headers = {"User-Agent": f"tak-feeder-weather-fmi/{__version__}"}
        self.stations: list[fmi.Station] = []
        self.radiation_stations: list[fmi.Station] = []
        # monotonic time of the last good fetch of each query
        self.stations_at: Optional[float] = None
        self.radiation_at: Optional[float] = None
        self.grid: Optional[fingrid.State] = None
        self.grid_since: Optional[datetime] = None  # when the current state began
        self.grid_at: Optional[float] = None

    def _local(self, when: Optional[datetime]) -> Optional[str]:
        return when.astimezone(self.tz).strftime("%H:%M") if when else None

    def weather_alerts(self, st: fmi.Station, now: datetime) -> list[alerts.Alert]:
        """Alerts of one weather station right now, highest level first."""
        return alerts.evaluate(st.values, st.lat, st.time or now, self.s.alerts)

    def weather_event(self, st: fmi.Station, now: datetime) -> bytes:
        """CoT for one weather station. A normal station is a plain marker; an
        alert changes the icon (tinted with the level colour) and callsign."""
        found = self.weather_alerts(st, now)
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
            stale=self.s.send.stale_for(self.s.send.static),
            how="m-c",
            icon=top.icon if top else "",
            color=top.color if top else "",
            now=now,
        )

    @staticmethod
    def dose_rate(st: fmi.Station) -> Optional[float]:
        """10-minute average dose rate (µSv/h) of a radiation station."""
        return st.values.get("DR_PT10M_avg", st.values.get("DRS1_PT10M_avg"))

    def radiation_alerting(self, st: fmi.Station) -> bool:
        """True if the station is at or above RADIATION_ALERT_LEVEL."""
        dose = self.dose_rate(st)
        return dose is not None and dose >= self.s.radiation_alert

    def radiation_event(self, st: fmi.Station, now: datetime) -> Optional[bytes]:
        """CoT for one radiation station (dose rate in µSv/h)."""
        dose = self.dose_rate(st)
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
            stale=self.s.send.stale_for(self.s.send.static),
            how="m-c",
            icon=self.s.radiation_alert_icon if alert else "",
            color=self.s.radiation_alert_color if alert else "",
            now=now,
        )

    def fingrid_alerting(self) -> bool:
        """True if the power system state is at FINGRID_ALERT_LEVEL or above."""
        return self.grid is not None and self.grid.value >= self.s.fingrid_alert_level

    def fingrid_event(self, now: datetime) -> Optional[bytes]:
        """CoT for the national power system state (one marker)."""
        if self.grid is None:
            return None
        value = self.grid.value
        name, meaning = fingrid.STATES[value]
        alert = self.fingrid_alerting()
        lines = [
            f"Power system state: {name.upper()} ({meaning})",
            f"Since: {self.grid_since.astimezone(self.tz):%d.%m. %H:%M}" if self.grid_since else None,
            "State of the whole Finnish power system; the marker position is not a location.",
        ]
        tags = ["fingrid", "power"] + (["alert"] if alert else [])
        return cot_event(
            uid="fingrid.powersystem",
            cot_type=FINGRID_TYPE,
            lat=self.s.fingrid_lat, lon=self.s.fingrid_lon,
            callsign=f"FINGRID Power system {name.upper()}",
            remarks=remarks_text(lines, self.s.fingrid_source_line, tags),
            stale=self.s.send.stale_for(self.s.send.static),
            how="m-c",
            icon=FINGRID_ICON if value != fingrid.GREEN else "",
            color=fingrid.COLORS.get(value, ""),
            now=now,
        )

    async def _fetch_fingrid(self, now: datetime, mono: float) -> None:
        """Latest power system state. Failures are logged only: the weather
        and radiation fetches decide the container health."""
        try:
            body = await fetch(fingrid.LATEST_URL, {**self.headers, "x-api-key": self.s.fingrid_key},
                               timeout=30)
            state = fingrid.parse(body)
        except Exception as exc:  # noqa: BLE001  pylint: disable=broad-exception-caught
            LOG.warning("Fingrid request failed: %s", exc)
            return
        if self.grid is None or state.value != self.grid.value:
            if self.grid is not None:
                LOG.info("Fingrid power system state %d -> %d", self.grid.value, state.value)
            self.grid_since = state.since or now
        self.grid, self.grid_at = state, mono

    async def _get(self, url: str) -> Optional[bytes]:
        try:
            return await fetch(url, self.headers, timeout=60)
        except Exception as exc:  # noqa: BLE001  pylint: disable=broad-exception-caught
            LOG.warning("FMI request failed: %s", exc)
            return None

    def _due(self, fetched_at: Optional[float], mono: float) -> bool:
        return fetched_at is None or mono - fetched_at >= self.s.fetch_interval

    def _fresh(self, fetched_at: Optional[float], mono: float) -> bool:
        return (fetched_at is not None
                and mono - fetched_at <= self.CACHE_ROUNDS * self.s.fetch_interval)

    async def _fetch_weather(self, now: datetime, mono: float) -> bool:
        url = fmi.query_url(WEATHER_QUERY, now, self.s.history_minutes,
                            bbox=self.s.bbox, parameters=self.s.parameters, timestep="10")
        body = await self._get(url)
        try:
            stations = fmi.parse(body) if body else []
        except Exception as exc:  # noqa: BLE001  pylint: disable=broad-exception-caught
            LOG.warning("FMI weather response not understood: %s", exc)
            stations = []
        if not stations:
            return False
        self.stations, self.stations_at = stations, mono
        return True

    async def _fetch_radiation(self, now: datetime, mono: float) -> bool:
        body = await self._get(fmi.query_url(RADIATION_QUERY, now, 0, bbox=self.s.bbox))
        if body is None:
            return False
        try:
            stations = fmi.parse(body)
        except Exception as exc:  # noqa: BLE001  pylint: disable=broad-exception-caught
            LOG.warning("STUK radiation response not understood: %s", exc)
            return False
        self.radiation_stations, self.radiation_at = stations, mono
        return True

    async def events(self) -> list[bytes]:
        """Source protocol: fetch when due, then convert the latest data."""
        now = datetime.now(timezone.utc)
        mono = time.monotonic()
        started = mono
        fetched = False
        failed = False

        if self.s.weather and self._due(self.stations_at, mono):
            fetched = True
            failed = not await self._fetch_weather(now, mono)
        if self.s.radiation and self._due(self.radiation_at, mono):
            fetched = True
            rad_failed = not await self._fetch_radiation(now, mono)
            # Radiation only counts for health when it is all this feeder does
            if not self.s.weather:
                failed = rad_failed

        # ONLY_ALERTS: send only the stations in alert state. Their CoT is the
        # same as in the full feed (same UID), so a user who also sees the full
        # feed gets one marker; when the alert ends the marker goes stale.
        events: list[bytes] = []
        weather_count = rad_count = 0
        if self.s.weather and self._fresh(self.stations_at, mono):
            for st in self.stations:
                if not self.s.only_alerts or self.weather_alerts(st, now):
                    events.append(self.weather_event(st, now))
            weather_count = len(self.stations)
        if self.s.radiation and self._fresh(self.radiation_at, mono):
            for st in self.radiation_stations:
                if self.s.only_alerts and not self.radiation_alerting(st):
                    continue
                event = self.radiation_event(st, now)
                if event:
                    events.append(event)
            rad_count = sum(1 for st in self.radiation_stations if self.dose_rate(st) is not None)
        if not self.s.weather and fetched and not rad_count:
            failed = True
        if self.s.fingrid_key:
            if self.grid_at is None or mono - self.grid_at >= self.s.fingrid_interval:
                await self._fetch_fingrid(now, mono)
            fresh = (self.grid_at is not None
                     and mono - self.grid_at <= self.CACHE_ROUNDS * self.s.fingrid_interval)
            if fresh and (not self.s.only_alerts or self.fingrid_alerting()):
                event = self.fingrid_event(now)
                if event:
                    events.append(event)

        if fetched:
            LOG.info("Weather: %d stations, radiation: %d, sent %d (%.1f s)",
                     weather_count, rad_count, len(events), time.monotonic() - started)
            if self.health is not None:
                if failed:
                    self.health.error()
                else:
                    self.health.ok()
        else:
            LOG.debug("Re-sent %d (weather: %d stations, radiation: %d)",
                      len(events), weather_count, rad_count)
        return events

    async def start(self) -> None:
        """Nothing runs in the background."""
