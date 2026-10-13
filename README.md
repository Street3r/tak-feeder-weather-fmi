# tak-feeder-weather-fmi

> [!WARNING]
> **Work in progress. Not recommended for use.** This fork is being improved
> and has not been tested against a live TAK server yet. Things may change or
> break without notice.

Sends the latest weather observations of the Finnish Meteorological
Institute (FMI) stations to a TAK server as CoT. Optionally also the external
radiation dose rates of STUK's monitoring stations.

Fork of [sgofferj/tak-feeder-weather-fmi](https://github.com/sgofferj/tak-feeder-weather-fmi)
by Stefan Gofferje.

## What it sends

| Field | Weather station | Radiation station (`RADIATION=1`) |
|---|---|---|
| UID | `fmisensor.<fmisid>` (as upstream) | `fmiradiation.<fmisid>` |
| Callsign | `FMI <station>` | `STUK <station>`, `… ALERT` above the alert level |
| Type | `b-w-A-t` | `a-n-G` |
| Remarks | temperature, wind and gusts, humidity, pressure, visibility, snow depth, observation time; `#weather #FMI` | dose rate (µSv/h, 10 min average), observation time; `#radiation #STUK` (+ `#alert`) |

Missing values are left out. Each round fetches the last `HISTORY_MINUTES`
minutes, so the data stays current.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `COT_URL` | `udp://239.2.3.1:6969` | TAK server, e.g. `ssl://takserver:8089` |
| `PYTAK_TLS_CLIENT_CERT` / `PYTAK_TLS_CLIENT_KEY` | | Client certificate and unencrypted key (PEM) |
| `PYTAK_TLS_DONT_VERIFY` | `0` | `1` = do not verify the server certificate |
| `TAK_PROTO` | | pytak protocol option, as upstream |
| `FMI_BBOX` | `18,59,33,71` | Area (west,south,east,north). Upstream used 60°N, which left out the southern coast (Hanko, Utö) |
| `WEATHER_PARAMETERS` | `t2m,ws_10min,wg_10min,wd_10min,rh,p_sea,vis,snow_aws` | FMI parameters |
| `HISTORY_MINUTES` | `60` | Look-back for the latest value of each station |
| `RADIATION` | `0` | `1` = also STUK radiation stations |
| `RADIATION_ALERT_LEVEL` | `0.4` | µSv/h; stations at or above it get `ALERT` and `#alert` |
| `UPDATE_INTERVAL` | `300` | Seconds between rounds (FMI updates every 10 min) |
| `STALE_SECONDS` | `900` | Marker lifetime without new data |
| `TIMEZONE` | `Europe/Helsinki` | Time zone of the observation time in the remarks |
| `HEALTH_MAX_ERRORS` | `3` | Failed rounds in a row before the container is unhealthy |
| `DEBUG` | `0` | Debug logging |

## Running

Image for amd64 and arm64: `ghcr.io/street3r/tak-feeder-weather-fmi:latest`.

```yaml
services:
  feeder-weather:
    image: ghcr.io/street3r/tak-feeder-weather-fmi:latest
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./certs:/data:ro
```

The container runs as uid 10001; the key must be readable by it.

## Data

Weather observations: Finnish Meteorological Institute open data,
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Radiation: STUK
(Radiation and Nuclear Safety Authority) via FMI open data, CC BY 4.0. The
source line is in every marker's remarks.

## Credits and changes

Original work © Stefan Gofferje,
[tak-feeder-weather-fmi](https://github.com/sgofferj/tak-feeder-weather-fmi),
used with his permission. The WFS parsing follows his
[trakbridge-plugin-lightnings-fmi](https://github.com/sgofferj/trakbridge-plugin-lightnings-fmi).

Changes in this fork (Street3r):

- 2026-10-13
  - **Fixed:** the query time window was computed once at start, so after the
    first 20 minutes the feeder kept sending the same old observations.
  - **Fixed:** an FMI outage or bad response no longer stops the feeder.
  - **Fixed:** missing temperature showed as `nan°C`; missing values are left out.
  - `fmiopendata` replaced with an own WFS parser; requests run without
    blocking.
  - More values: gusts, humidity, pressure, visibility, snow depth, observation
    time. Callsign `FMI <station>`. Area extended to 59°N.
  - Optional STUK radiation stations (the unfinished upstream code is replaced).
  - `UPDATE_INTERVAL`, `STALE_SECONDS`; health check; unprivileged container;
    tests and CI.

## License

GNU General Public License v3.0 or later, see [LICENSE](LICENSE).
