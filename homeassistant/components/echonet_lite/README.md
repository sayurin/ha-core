# HEMS Echonet Lite Integration

This document covers details that new contributors may find helpful when getting
started with the HEMS Echonet Lite integration.

## About the name

The integration is registered under the domain `echonet_lite` but is officially
called **HEMS Echonet Lite integration**. ECHONET Lite is a protocol primarily
used in Japan for [HEMS][hems] (Home Energy Management System) devices such as
smart meters, batteries, heat-pump water heaters, solar inverters and air
conditioners under the [ZEH][zeh] / [ECHONET Consortium][echonet] specifications.
The `HEMS` prefix in the human-readable name conveys this Japanese smart-home
context and disambiguates against unrelated "ECHONET" products. The domain
itself omits the prefix because the underlying protocol is ECHONET Lite.

[hems]: https://www.meti.go.jp/policy/energy_environment/energy_efficiency/hems/
[zeh]: https://www.enecho.meti.go.jp/category/saving_and_new/saving/general/housing/index03.html
[echonet]: https://echonet.jp/spec_g/

## Why `iot_class` is `local_polling`

ECHONET Lite is fundamentally a request/response protocol over local UDP
multicast: status is read with `GET` (ESV `0x62`) and answered with `GET_RES`
(`0x72`). The specification also defines unsolicited notifications `INF`
(`0x73`) and `INFC` (`0x74`), but their use is constrained by APPENDIX
"Detailed Requirements for ECHONET Device Objects": every property carries an
"Announcement at Status Change" flag, and only properties bearing that flag may
be pushed. The flag is set on a minority of properties — typically operating
status and a handful of mode/state EPCs — while measured values, numeric set
points and most configuration items can only be obtained via `GET`. Whether a
vendor actually emits `INF` for its announceable properties is additionally
left to the implementation.

As a consequence this integration is polling-first: `PropertyPoller` runs a
per-device `GET` loop that starts from `DEFAULT_POLL_INTERVAL` (60 s) and then
adapts based on observed latency and failures, while keeping a separate
high-frequency lane for instantaneous EPCs. `INF` frames — when they arrive —
are treated as a free push-based optimization on top. The poll set is
automatically reduced to `get_epcs - inf_epcs` per device, and then narrowed
again by the effective subscribed EPC set so disabled entities are not
re-polled. The `iot_class` therefore stays `local_polling` even though the
runtime opportunistically reacts to pushed frames.

## Generated translation strings

`strings.json` is generated. It is assembled from two sources:

- ECHONET Lite property/enum metadata loaded from the `pyhems` definitions
  registry (which itself is generated from the [ECHONET Consortium MRA][mra]).
- The static fragments in
  [`scripts/strings_static.json`](scripts/strings_static.json) that cover
  config flow and repair issues — i.e. text the registry does
  not own.

Do **not** edit `strings.json` directly. Instead, update
[`scripts/generate_strings.py`](scripts/generate_strings.py) (for the
generation logic) or `scripts/strings_static.json` (for static text), and then
regenerate:

```bash
python -m homeassistant.components.echonet_lite.scripts.generate_strings
```

The `scripts/` directory is **not** on the runtime import path: it only ships
the generator. The generated `strings.json` is committed so reviewers and CI
do not need to run the generator.

[mra]: https://echonet.jp/spec_mra_rp1/

## Architecture overview

```
+----------------+      UDP multicast       +-----------------+
|   pyhems       |  <---------------------> | ECHONET Lite    |
|   HemsClient   |                          | devices (LAN)   |
+--------+-------+                          +-----------------+
         |
         | RuntimeEvent  (frames, instance list, errors)
         v
+--------+-------+    on_device_added/updated    +------------------------+
| DeviceManager  | ----------------------------> | EchonetLiteCoordinator |
| (pyhems)       |                                | (DataUpdateCoordinator)|
+--------+-------+                                +-----------+------------+
         ^                                                    |
         | poll EPCs                                          v
+--------+-------+                                +-----------+------------+
| PropertyPoller |                                | Entities (12 platforms)|
| (pyhems)       |                                +------------------------+
+----------------+
```

`async_setup_entry` constructs these objects and hands them to
`RuntimeController`, which owns the runtime lifecycle (subscribe, start,
discovery task, event-consumer task, adaptive property polling,
restart-on-error) behind symmetric `async_start`/`async_stop` methods. The
coordinator's `update_interval` is `None` because data arrives via runtime
events rather than scheduled refreshes.

## Tests

```bash
python -m pytest tests/components/echonet_lite
```
