# Agent Notes

This repository contains AutoDRIVE Race Control Tower (RCT), a Socket.IO-aware proxy for the AutoDRIVE RoboRacer H2H Simulator.

## Project Rules

- Keep the runtime on Python 3.12 with `aiohttp` and `python-socketio`.
- RCT accepts simulator Socket.IO clients on `/socket.io/` and browser monitor clients on `/monitor/WS/latest` or `/monitor/WS/0.1`.
- RCT connects upstream to DevKit bridge instances listed in `RCT_DEVKIT_URLS`.
- RCT connects upstream as a Socket.IO client and forces the WebSocket transport.
- `RCT_DEVKIT_URLS` may use `ws://`, `wss://`, `http://`, or `https://`; normalize `ws://` to `http://` and `wss://` to `https://` before calling `python-socketio`.
- Each DevKit URL receives one simulator vehicle id from `RCT_DEVKIT_VEHICLE_IDS`.
- Simulator-to-DevKit messages must be rewritten from the assigned simulator id to id `1`.
- DevKit-to-Simulator messages must be rewritten from id `1` back to the assigned simulator id.
- Preserve original payload shape whenever possible. Only rewrite vehicle identifiers.
- Frontend observation events may use the RCT JSON envelope format.
- REST monitor responses and WS monitor events must read shared state from `RaceControlState`.
- Monitor WebSocket client fanout must go through `MonitorEventHub`.
- Do not perform network I/O while mutating shared race-control state. Update state first, then broadcast events.

## Supported Identifier Forms

- `roboracer_<id>` inside topic-like strings, for example `/autodrive/roboracer_2/ips`
- `V<id> ` field prefixes used by the AutoDRIVE bridge dictionary, for example `V2 Position`

## Run And Verify

- Local run: `python -m rct`
- Docker run: `docker run --rm -p 4567:4567 autodrive-rct`
- Frontend check: open `frontend/index.html` and inspect browser console connection logs.
- Protocol unit tests: `python3 -m unittest`

## Notes

- The public AutoDRIVE RoboRacer DevKit bridge example uses Socket.IO; RCT should preserve Socket.IO event names and payload shape whenever possible.
- Binary payloads are forwarded without id rewriting.
- Camera and LIDAR frames can be large; tune `RCT_MAX_MESSAGE_SIZE` for the deployment.
- Do not revert user changes. Leave unrelated cleanup for a separate request.
