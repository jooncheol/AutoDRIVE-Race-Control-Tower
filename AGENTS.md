# Agent Notes

This repository contains AutoDRIVE Race Control Tower (RCT), a native WebSocket proxy for the AutoDRIVE RoboRacer H2H Simulator.

## Project Rules

- Keep the runtime on Python 3.12 and the `websockets` package.
- RCT accepts simulator clients on `/simulator` and browser frontend clients on `/frontend`.
- RCT connects upstream to DevKit bridge instances listed in `RCT_DEVKIT_URLS`.
- Each DevKit URL receives one simulator vehicle id from `RCT_DEVKIT_VEHICLE_IDS`.
- Simulator-to-DevKit messages must be rewritten from the assigned simulator id to id `1`.
- DevKit-to-Simulator messages must be rewritten from id `1` back to the assigned simulator id.
- Preserve original payload shape whenever possible. Only rewrite vehicle identifiers.
- Frontend observation events may use the RCT JSON envelope format.

## Supported Identifier Forms

- `roboracer_<id>` inside topic-like strings, for example `/autodrive/roboracer_2/ips`
- `V<id> ` field prefixes used by the AutoDRIVE bridge dictionary, for example `V2 Position`

## Run And Verify

- Local run: `python -m rct`
- Docker run: `docker run --rm -p 8765:8765 autodrive-rct`
- Frontend check: open `frontend/index.html` and inspect browser console connection logs.
- Protocol unit tests: `python3 -m unittest`

## Notes

- The public AutoDRIVE RoboRacer DevKit bridge example uses Socket.IO. This project intentionally uses native WebSocket because that is the requested transport.
- Binary frames are forwarded without id rewriting.
- Camera and LIDAR frames can be large; tune `RCT_MAX_MESSAGE_SIZE` for the deployment.
- Do not revert user changes. Leave unrelated cleanup for a separate request.

