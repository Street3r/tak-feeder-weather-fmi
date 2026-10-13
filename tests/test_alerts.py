# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for weather alerts."""

import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from tak_feeder_weather_fmi import alerts, feeder, fmi

JAN = datetime(2026, 1, 15, 12, tzinfo=timezone.utc)
JUL = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
HELSINKI, KUOPIO, SODANKYLA = 60.2, 62.9, 67.4


def kinds(values, lat=HELSINKI, when=JAN, **kw):
    s = alerts.AlertSettings(**kw)
    return [(a.kind, a.level) for a in alerts.evaluate(values, lat, when, s)]


class FrostTest(unittest.TestCase):
    def test_regions_orange(self) -> None:
        self.assertEqual(kinds({"t2m": -30.0}), [("frost", alerts.ORANGE)])
        self.assertEqual(kinds({"t2m": -30.0}, KUOPIO), [])  # central: orange at -35
        self.assertEqual(kinds({"t2m": -36.0}, KUOPIO), [("frost", alerts.ORANGE)])
        self.assertEqual(kinds({"t2m": -39.0}, SODANKYLA), [])  # north: orange at -40
        self.assertEqual(kinds({"t2m": -45.0}, SODANKYLA), [("frost", alerts.RED)])

    def test_yellow_hidden_by_default(self) -> None:
        self.assertEqual(kinds({"t2m": -22.0}), [])
        self.assertEqual(kinds({"t2m": -22.0}, min_level=alerts.YELLOW),
                         [("frost", alerts.YELLOW)])

    def test_wind_chill(self) -> None:
        # -22 °C and 8 m/s (28.8 km/h) feels like about -35 °C: orange in the south
        self.assertAlmostEqual(alerts.wind_chill(-22.0, 8.0), -35.0, delta=0.1)
        self.assertEqual(kinds({"t2m": -22.0, "ws_10min": 8.0}), [("frost", alerts.ORANGE)])
        self.assertIsNone(alerts.wind_chill(15.0, 8.0))
        self.assertIsNone(alerts.wind_chill(-10.0, 1.0))


class WindTest(unittest.TestCase):
    def test_gusts_by_season(self) -> None:
        self.assertEqual(kinds({"wg_10min": 24.0}, when=JAN), [])
        self.assertEqual(kinds({"wg_10min": 25.0}, when=JAN), [("wind", alerts.ORANGE)])
        self.assertEqual(kinds({"wg_10min": 20.0}, when=JUL), [("wind", alerts.ORANGE)])
        self.assertEqual(kinds({"wg_10min": 31.0}, when=JAN), [("wind", alerts.RED)])

    def test_mean_wind(self) -> None:
        self.assertEqual(kinds({"ws_10min": 21.0, "wg_10min": 22.0}), [("wind", alerts.ORANGE)])
        self.assertEqual(kinds({"ws_10min": 33.0}), [("wind", alerts.RED)])


class OtherTest(unittest.TestCase):
    def test_heat(self) -> None:
        self.assertEqual(kinds({"t2m": 29.9}, when=JUL), [])
        self.assertEqual(kinds({"t2m": 30.0}, when=JUL), [("heat", alerts.ORANGE)])

    def test_visibility(self) -> None:
        self.assertEqual(kinds({"vis": 900.0}), [])
        self.assertEqual(kinds({"vis": 150.0}), [("visibility", alerts.ORANGE)])
        self.assertEqual(kinds({"vis": 40.0}), [("visibility", alerts.RED)])

    def test_freezing(self) -> None:
        self.assertEqual(kinds({"wawa": 65.0}), [("freezing", alerts.ORANGE)])
        self.assertEqual(kinds({"wawa": 61.0}), [])
        self.assertEqual(kinds({"wawa": 65.0}, freezing=False), [])

    def test_order_and_disable(self) -> None:
        values = {"t2m": -36.0, "wg_10min": 31.0, "vis": 150.0}
        # same level: wind before frost (PRIORITY)
        self.assertEqual(kinds(values), [("wind", alerts.RED), ("frost", alerts.RED),
                                         ("visibility", alerts.ORANGE)])
        self.assertEqual(kinds(values, enabled=["visibility"]),
                         [("visibility", alerts.ORANGE)])

    def test_levels_parsing(self) -> None:
        self.assertEqual(alerts._levels("1,2,3", (0, 0, 0)), (1.0, 2.0, 3.0))
        self.assertEqual(alerts._levels("x", (4, 5, 6)), (4, 5, 6))


class EventTest(unittest.TestCase):
    def station(self, **values):
        st = fmi.Station("101004", "Helsinki Kumpula", HELSINKI, 24.96, JAN)
        st.values = values
        return st

    def test_normal_station_is_plain(self) -> None:
        src = feeder.WeatherSource(feeder.Settings())
        ev = ET.fromstring(src.weather_event(self.station(t2m=-5.0), JAN))
        self.assertIsNone(ev.find("detail/usericon"))
        self.assertIsNone(ev.find("detail/color"))
        self.assertEqual(ev.find("detail/contact").get("callsign"), "FMI Helsinki Kumpula")

    def test_alert_changes_icon(self) -> None:
        src = feeder.WeatherSource(feeder.Settings())
        ev = ET.fromstring(src.weather_event(self.station(t2m=-31.0, wg_10min=26.0), JAN))
        self.assertEqual(ev.find("detail/usericon").get("iconsetpath"), alerts.ICONS["wind"])
        self.assertEqual(ev.find("detail/color").get("argb"), alerts.LEVEL_COLORS[alerts.ORANGE])
        self.assertEqual(ev.find("detail/contact").get("callsign"), "FMI Helsinki Kumpula WIND")
        text = ev.find("detail/remarks").text
        self.assertTrue(text.startswith("ALERT (orange) Wind: gusts 26.0 m/s"))
        self.assertIn("ALERT (orange) Frost:", text)
        self.assertTrue(text.endswith("#weather #FMI #alert #wind #frost"))
        self.assertEqual(ev.get("type"), feeder.WEATHER_TYPE)


if __name__ == "__main__":
    unittest.main()
