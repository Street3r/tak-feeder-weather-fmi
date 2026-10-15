# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests for the FMI parser and the weather feeder."""

import asyncio
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from unittest import mock

from tak_feeder_weather_fmi import feeder, fmi

T1 = 1760352000  # 2025-10-13T10:40:00Z
T2 = T1 + 600

SAMPLE = f"""<?xml version="1.0" encoding="UTF-8"?>
<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0"
  xmlns:om="http://www.opengis.net/om/2.0" xmlns:omso="http://inspire.ec.europa.eu/schemas/omso/3.0"
  xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:swe="http://www.opengis.net/swe/2.0"
  xmlns:gmlcov="http://www.opengis.net/gmlcov/1.0" xmlns:sam="http://www.opengis.net/sampling/2.0"
  xmlns:sams="http://www.opengis.net/samplingSpatial/2.0" xmlns:xlink="http://www.w3.org/1999/xlink"
  xmlns:target="http://xml.fmi.fi/namespace/om/atmosphericfeatures/1.1">
 <wfs:member><omso:GridSeriesObservation gml:id="obs-1">
  <om:featureOfInterest><sams:SF_SpatialSamplingFeature gml:id="sf-1">
   <sam:sampledFeature><target:LocationCollection gml:id="lc-1">
    <target:member><target:Location gml:id="obsloc-fmisid-101004-pos">
      <gml:identifier codeSpace="http://xml.fmi.fi/namespace/stationcode/fmisid">101004</gml:identifier>
      <gml:name codeSpace="http://xml.fmi.fi/namespace/locationcode/name">Helsinki Kumpula</gml:name>
      <gml:name codeSpace="http://xml.fmi.fi/namespace/locationcode/geoid">-16000150</gml:name>
      <target:representativePoint xlink:href="#point-101004"/>
    </target:Location></target:member>
    <target:member><target:Location gml:id="obsloc-fmisid-100996-pos">
      <gml:identifier codeSpace="http://xml.fmi.fi/namespace/stationcode/fmisid">100996</gml:identifier>
      <gml:name codeSpace="http://xml.fmi.fi/namespace/locationcode/name">Helsinki Harmaja</gml:name>
      <target:representativePoint xlink:href="#point-100996"/>
    </target:Location></target:member>
   </target:LocationCollection></sam:sampledFeature>
   <sams:shape><gml:MultiPoint gml:id="mp-1">
     <gml:pointMember><gml:Point gml:id="point-101004" srsDimension="2">
       <gml:name>Helsinki Kumpula</gml:name><gml:pos>60.20307 24.96131 </gml:pos></gml:Point></gml:pointMember>
     <gml:pointMember><gml:Point gml:id="point-100996" srsDimension="2">
       <gml:name>Helsinki Harmaja</gml:name><gml:pos>60.10512 24.97539 </gml:pos></gml:Point></gml:pointMember>
   </gml:MultiPoint></sams:shape>
  </sams:SF_SpatialSamplingFeature></om:featureOfInterest>
  <om:result><gmlcov:MultiPointCoverage gml:id="mpcv-1">
   <gml:domainSet><gmlcov:SimpleMultiPoint gml:id="smp-1" srsDimension="3">
    <gmlcov:positions>
                60.20307 24.96131  {T1}
                60.20307 24.96131  {T2}
                60.10512 24.97539  {T1}
                60.10512 24.97539  {T2}
                </gmlcov:positions>
   </gmlcov:SimpleMultiPoint></gml:domainSet>
   <gml:rangeSet><gml:DataBlock><gml:rangeParameters/>
    <gml:doubleOrNilReasonTupleList>
                8.1 3.0 NaN 220.0 85.0 1012.4 35000.0 -1.0
                8.4 3.2 5.1 225.0 84.0 1012.3 NaN -1.0
                9.0 7.5 9.9 230.0 90.0 NaN NaN NaN
                NaN NaN NaN NaN NaN NaN NaN NaN
                </gml:doubleOrNilReasonTupleList>
   </gml:DataBlock></gml:rangeSet>
   <gmlcov:rangeType><swe:DataRecord>
    <swe:field name="t2m" xlink:href="x"/><swe:field name="ws_10min" xlink:href="x"/>
    <swe:field name="wg_10min" xlink:href="x"/><swe:field name="wd_10min" xlink:href="x"/>
    <swe:field name="rh" xlink:href="x"/><swe:field name="p_sea" xlink:href="x"/>
    <swe:field name="vis" xlink:href="x"/><swe:field name="snow_aws" xlink:href="x"/>
   </swe:DataRecord></gmlcov:rangeType>
  </gmlcov:MultiPointCoverage></om:result>
 </omso:GridSeriesObservation></wfs:member>
</wfs:FeatureCollection>""".encode()


class ParseTest(unittest.TestCase):
    def test_latest_non_missing_values(self) -> None:
        stations = {s.fmisid: s for s in fmi.parse(SAMPLE)}
        kumpula = stations["101004"]
        self.assertEqual(kumpula.name, "Helsinki Kumpula")
        self.assertAlmostEqual(kumpula.lat, 60.20307)
        self.assertEqual(kumpula.values["t2m"], 8.4)
        self.assertEqual(kumpula.values["vis"], 35000.0)  # last non-NaN value
        self.assertEqual(kumpula.time, datetime.fromtimestamp(T2, tz=timezone.utc))
        harmaja = stations["100996"]
        self.assertEqual(harmaja.values["t2m"], 9.0)  # the later row is all NaN
        self.assertNotIn("p_sea", harmaja.values)

    def test_query_url_has_moving_time_window(self) -> None:
        url1 = fmi.query_url("q", datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc), 60)
        url2 = fmi.query_url("q", datetime(2026, 10, 13, 13, 0, tzinfo=timezone.utc), 60)
        self.assertIn("starttime=2026-10-13T11:00:00Z", url1)
        self.assertIn("endtime=2026-10-13T13:00:00Z", url2)
        self.assertNotIn("starttime", fmi.query_url("q", datetime.now(timezone.utc), 0))

    def test_empty_response(self) -> None:
        self.assertEqual(fmi.parse(b"<wfs:FeatureCollection xmlns:wfs='x'/>"), [])


class FeederTest(unittest.TestCase):
    def test_events(self) -> None:
        src = feeder.WeatherSource(feeder.Settings())
        with mock.patch.object(feeder, "fetch", mock.AsyncMock(return_value=SAMPLE)) as m:
            events = [ET.fromstring(e) for e in asyncio.run(src.events())]
        url = m.call_args.args[0]
        self.assertIn("bbox=18,59,33,71", url)
        self.assertIn("parameters=t2m,ws_10min", url)
        self.assertEqual(len(events), 2)
        ev = {e.get("uid"): e for e in events}["fmisensor.101004"]
        self.assertEqual(ev.find("detail/contact").get("callsign"), "FMI Helsinki Kumpula")
        text = ev.find("detail/remarks").text
        self.assertIn("Temperature: 8.4 °C", text)
        self.assertIn("Wind: 3.2 m/s, gusts 5.1 m/s, from 225°", text)
        self.assertIn("Visibility: 35.0 km", text)
        self.assertNotIn("Snow depth", text)  # -1 = no snow measurement
        self.assertNotIn("nan", text.lower())
        self.assertTrue(text.endswith("#weather #FMI"))

    def test_fmi_down_is_not_fatal(self) -> None:
        src = feeder.WeatherSource(feeder.Settings())
        with mock.patch.object(feeder, "fetch", mock.AsyncMock(side_effect=OSError("down"))):
            self.assertEqual(asyncio.run(src.events()), [])

    def test_fetches_every_fetch_interval_and_resends_between(self) -> None:
        src = feeder.WeatherSource(feeder.Settings(fetch_interval=300))
        clock = mock.Mock(return_value=1000.0)
        with mock.patch.object(feeder, "fetch", mock.AsyncMock(return_value=SAMPLE)) as m, \
                mock.patch.object(feeder.time, "monotonic", clock):
            first = asyncio.run(src.events())
            clock.return_value = 1060.0
            second = asyncio.run(src.events())
            self.assertEqual(m.await_count, 1)  # re-sent from the cache
            clock.return_value = 1300.0
            asyncio.run(src.events())
            self.assertEqual(m.await_count, 2)
        uids = sorted(ET.fromstring(e).get("uid") for e in first)
        self.assertEqual(sorted(ET.fromstring(e).get("uid") for e in second), uids)

    def test_failed_fetch_resends_cache_for_three_fetch_intervals(self) -> None:
        src = feeder.WeatherSource(feeder.Settings(fetch_interval=300))
        clock = mock.Mock(return_value=1000.0)
        with mock.patch.object(feeder.time, "monotonic", clock):
            with mock.patch.object(feeder, "fetch", mock.AsyncMock(return_value=SAMPLE)):
                self.assertEqual(len(asyncio.run(src.events())), 2)
            down = mock.AsyncMock(side_effect=OSError("down"))
            with mock.patch.object(feeder, "fetch", down):
                clock.return_value = 1500.0
                self.assertEqual(len(asyncio.run(src.events())), 2)
                clock.return_value = 1560.0  # retried every round
                self.assertEqual(len(asyncio.run(src.events())), 2)
                self.assertEqual(down.await_count, 2)
                clock.return_value = 1901.0  # last good fetch 3 fetch intervals ago
                self.assertEqual(asyncio.run(src.events()), [])

    def test_radiation_only(self) -> None:
        sample = (SAMPLE.replace(b'name="t2m"', b'name="DR_PT10M_avg"')
                  .replace(b"8.4 3.2", b"0.10 3.2"))
        src = feeder.WeatherSource(feeder.Settings(weather=False, radiation=True))
        with mock.patch.object(feeder, "fetch", mock.AsyncMock(return_value=sample)) as m:
            events = [ET.fromstring(e) for e in asyncio.run(src.events())]
        self.assertEqual(m.await_count, 1)  # no weather query
        self.assertIn("stuk::", m.await_args.args[0])
        self.assertTrue(events)
        self.assertTrue(all(e.get("uid").startswith("fmiradiation.") for e in events))

    def test_radiation(self) -> None:
        sample = (SAMPLE.replace(b'name="t2m"', b'name="DR_PT10M_avg"')
                  .replace(b"8.4 3.2", b"0.45 3.2"))
        src = feeder.WeatherSource(feeder.Settings(radiation=True))
        st = {s.fmisid: s for s in fmi.parse(sample)}["101004"]
        ev = ET.fromstring(src.radiation_event(st, datetime.now(timezone.utc)))
        self.assertEqual(ev.find("detail/contact").get("callsign"), "STUK Helsinki Kumpula ALERT")
        self.assertIn("Dose rate: 0.450 µSv/h", ev.find("detail/remarks").text)
        self.assertIn("#alert", ev.find("detail/remarks").text)
        self.assertEqual(ev.find("detail/usericon").get("iconsetpath"), feeder.RADIATION_ALERT_ICON)
        self.assertEqual(ev.get("type"), "a-n-G")

    def test_radiation_normal_has_plain_marker(self) -> None:
        sample = (SAMPLE.replace(b'name="t2m"', b'name="DR_PT10M_avg"')
                  .replace(b"8.4 3.2", b"0.10 3.2"))
        src = feeder.WeatherSource(feeder.Settings(radiation=True))
        st = {s.fmisid: s for s in fmi.parse(sample)}["101004"]
        ev = ET.fromstring(src.radiation_event(st, datetime.now(timezone.utc)))
        self.assertEqual(ev.find("detail/contact").get("callsign"), "STUK Helsinki Kumpula")
        self.assertEqual(ev.get("type"), feeder.WEATHER_TYPE)  # same icon as weather stations
        self.assertIsNone(ev.find("detail/usericon"))
        self.assertIsNone(ev.find("detail/color"))

    def test_radiation_alert_options(self) -> None:
        sample = (SAMPLE.replace(b'name="t2m"', b'name="DR_PT10M_avg"')
                  .replace(b"8.4 3.2", b"0.45 3.2"))
        src = feeder.WeatherSource(feeder.Settings(
            radiation=True, radiation_alert_icon="x/Shapes/caution.png",
            radiation_alert_color="-65536", radiation_alert_type="b-l-c-n-r"))
        st = {s.fmisid: s for s in fmi.parse(sample)}["101004"]
        ev = ET.fromstring(src.radiation_event(st, datetime.now(timezone.utc)))
        self.assertEqual(ev.get("type"), "b-l-c-n-r")
        self.assertEqual(ev.find("detail/usericon").get("iconsetpath"), "x/Shapes/caution.png")
        self.assertEqual(ev.find("detail/color").get("argb"), "-65536")


if __name__ == "__main__":
    unittest.main()
