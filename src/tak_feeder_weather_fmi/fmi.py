# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Street3r
#
# Replaces the fmiopendata library used by sgofferj's tak-feeder-weather-fmi,
# following the WFS parsing of trakbridge-plugin-lightnings-fmi
# (Copyright Stefan Gofferje, GPL-3.0-or-later).
"""FMI open data WFS: multipointcoverage responses."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

WFS_URL = "https://opendata.fmi.fi/wfs"

NS = {
    "gml": "http://www.opengis.net/gml/3.2",
    "gmlcov": "http://www.opengis.net/gmlcov/1.0",
    "swe": "http://www.opengis.net/swe/2.0",
    "target": "http://xml.fmi.fi/namespace/om/atmosphericfeatures/1.1",
    "xlink": "http://www.w3.org/1999/xlink",
}
GML_ID = f"{{{NS['gml']}}}id"
XLINK_HREF = f"{{{NS['xlink']}}}href"


@dataclass
class Station:
    """Latest values of one station."""

    fmisid: str
    name: str
    lat: float
    lon: float
    time: Optional[datetime] = None
    values: dict[str, float] = field(default_factory=dict)


def query_url(stored_query: str, now: datetime, minutes: int, **params: str) -> str:
    """WFS getFeature URL for a stored query over the last `minutes`
    (0 = no time range, for the ::latest:: queries)."""
    args = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "getFeature",
        "storedquery_id": stored_query,
    }
    if minutes:
        args["starttime"] = (now - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:00Z")
        args["endtime"] = now.strftime("%Y-%m-%dT%H:%M:00Z")
    args.update(params)
    return f"{WFS_URL}?{urlencode(args, safe=',:')}"


def _stations(root: ET.Element) -> dict[str, Station]:
    """Stations by fmisid: names from target:Location, positions from gml:Point."""
    points: dict[str, tuple[float, float]] = {}
    for point in root.iter(f"{{{NS['gml']}}}Point"):
        pid = point.get(GML_ID, "")
        pos = point.find("gml:pos", NS)
        if pid.startswith("point-") and pos is not None and pos.text:
            lat, lon = (float(v) for v in pos.text.split()[:2])
            points[pid[len("point-"):]] = (lat, lon)

    stations: dict[str, Station] = {}
    for loc in root.iter(f"{{{NS['target']}}}Location"):
        ident = loc.find("gml:identifier", NS)
        if ident is None or not ident.text:
            continue
        fmisid = ident.text.strip()
        name = fmisid
        for el in loc.findall("gml:name", NS):
            if el.get("codeSpace", "").endswith("/name") and el.text:
                name = el.text.strip()
        ref = loc.find("target:representativePoint", NS)
        key = (ref.get(XLINK_HREF, "") if ref is not None else "").lstrip("#")
        key = key[len("point-"):] if key.startswith("point-") else fmisid
        if key in points:
            lat, lon = points[key]
            stations[fmisid] = Station(fmisid, name, lat, lon)
    return stations


def parse(xml: bytes) -> list[Station]:
    """Latest non-missing value of every parameter per station.

    Rows of the tuple list match the (lat, lon, unix time) triplets of
    gmlcov:positions; columns match the swe:field names.
    """
    root = ET.fromstring(xml)
    stations = _stations(root)
    by_pos = {(round(s.lat, 4), round(s.lon, 4)): s for s in stations.values()}

    pos_el = root.find(".//gmlcov:positions", NS)
    rows_el = root.find(".//gml:doubleOrNilReasonTupleList", NS)
    if pos_el is None or rows_el is None or not pos_el.text or not rows_el.text:
        return []
    fields = [f.get("name", "") for f in root.findall(".//swe:field", NS)]
    numbers = pos_el.text.split()
    triplets = [numbers[i:i + 3] for i in range(0, len(numbers) - 2, 3)]
    rows = [r.split() for r in rows_el.text.strip().splitlines() if r.strip()]

    # oldest first, so later values replace earlier ones
    for (lat, lon, epoch), row in sorted(zip(triplets, rows), key=lambda tr: float(tr[0][2])):
        station = by_pos.get((round(float(lat), 4), round(float(lon), 4)))
        if station is None:
            continue
        when = datetime.fromtimestamp(int(float(epoch)), tz=timezone.utc)
        for name, raw in zip(fields, row):
            try:
                value = float(raw)
            except ValueError:
                continue
            if math.isnan(value):
                continue
            station.values[name] = value
            if station.time is None or when > station.time:
                station.time = when
    return [s for s in stations.values() if s.values]
