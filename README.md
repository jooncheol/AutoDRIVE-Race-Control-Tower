# AutoDRIVE Race Control Tower

AutoDRIVE Race Control Tower (RCT) is a high-performance WebSocket proxy for the AutoDRIVE RoboRacer H2H Simulator. It lets one simulator interact with two independent DevKit bridge instances without changing the existing AutoDRIVE protocol or the DevKit bridge code.

## Purpose

The stock DevKit bridge expects to control `roboracer_1`. In a head-to-head simulator run, each DevKit instance still receives vehicle data as `roboracer_1`, while RCT maps each connected DevKit instance to a simulator vehicle id:

- Simulator to DevKit: vehicle-specific data for the assigned simulator vehicle is rewritten to id `1`.
- DevKit to Simulator: commands produced by the DevKit for id `1` are rewritten back to the assigned simulator vehicle id.

Example mapping:

- Simulator data `/autodrive/roboracer_2/ips` is sent to DevKit 2 as `/autodrive/roboracer_1/ips`.
- DevKit 2 command `/autodrive/roboracer_1/throttle_command` is sent to the simulator as `/autodrive/roboracer_2/throttle_command`.
- AutoDRIVE bridge dictionary fields such as `V2 Position` are sent to DevKit 2 as `V1 Position`.
- DevKit 2 fields such as `V1 Throttle` are sent back to the simulator as `V2 Throttle`.

## Connection Model

- RCT WebSocket server: `0.0.0.0:8765`
- AutoDRIVE Simulator client: `ws://<rct-host>:8765/simulator`
- RCT browser frontend client: `ws://<rct-host>:8765/frontend`
- DevKit upstream 1: configured by `RCT_DEVKIT_URLS`
- DevKit upstream 2: configured by `RCT_DEVKIT_URLS`

By default, DevKit 1 is assigned simulator vehicle id `1` and DevKit 2 is assigned simulator vehicle id `2`.

## Requirements

- Python 3.12+
- `websockets==16.0`
- Docker, optional

## Install

```bash
python3 -m pip install -r requirements.txt
```

The host workspace does not provide `pip` or `ensurepip`, so the freeze was verified inside the Docker image. The resulting `pip freeze` output is:

```text
websockets==16.0
```

## Run Locally

```bash
export RCT_DEVKIT_URLS="ws://127.0.0.1:4567,ws://127.0.0.1:4568"
export RCT_DEVKIT_VEHICLE_IDS="1,2"
python3 -m rct
```

Environment variables:

| Name | Default | Description |
| --- | --- | --- |
| `RCT_HOST` | `0.0.0.0` | RCT WebSocket bind host |
| `RCT_PORT` | `8765` | RCT WebSocket port |
| `RCT_DEVKIT_URLS` | `ws://127.0.0.1:4567,ws://127.0.0.1:4568` | Comma-separated DevKit WebSocket URLs |
| `RCT_DEVKIT_VEHICLE_IDS` | `1,2,...` | Comma-separated simulator vehicle ids assigned to each DevKit URL |
| `RCT_RECONNECT_DELAY_SECONDS` | `3.0` | Delay before reconnecting to a DevKit endpoint |
| `RCT_MAX_MESSAGE_SIZE` | `16777216` | Maximum WebSocket message size. Use `0` or less for no limit |
| `RCT_CLIENT_QUEUE_SIZE` | `256` | Outbound queue size per DevKit connection |
| `RCT_PING_INTERVAL_SECONDS` | `20` | WebSocket ping interval |
| `RCT_PING_TIMEOUT_SECONDS` | `20` | WebSocket ping timeout |

## Frontend

Open `frontend/index.html` in a browser. The page uses Bootstrap and logs only the RCT WebSocket connection state to the browser console.

## Docker

Build the image:

```bash
docker build -t autodrive-rct .
```

Run the container:

```bash
docker run --rm \
  -p 8765:8765 \
  -e RCT_DEVKIT_URLS="ws://host.docker.internal:4567,ws://host.docker.internal:4568" \
  -e RCT_DEVKIT_VEHICLE_IDS="1,2" \
  autodrive-rct
```

On Linux, add `--add-host=host.docker.internal:host-gateway` if `host.docker.internal` is not available, or place RCT and the DevKit instances on the same Docker network.

## Frontend Command Format

The bundled frontend does not send commands by default. For manual testing, the browser console can send JSON commands:

```javascript
socket.send(JSON.stringify({
  target: "devkit:2",
  payload: { "V2 Position": "1.0 2.0 0.0" }
}));
```

Supported targets:

- `simulator`
- `all-devkits`
- `devkit:1`
- `devkit:2`

When targeting DevKit connections, RCT applies the same simulator-to-DevKit id rewrite before sending the payload upstream.

## Protocol Notes

RCT handles both common AutoDRIVE id forms:

- ROS-style strings containing `roboracer_<id>`, such as `/autodrive/roboracer_2/ips`
- DevKit bridge dictionary fields using `V<id> ` prefixes, such as `V2 LIDAR Range Array`

Binary frames are forwarded without id rewriting. Use text or JSON frames when id rewriting is required.

The referenced AutoDRIVE RoboRacer DevKit bridge currently uses Socket.IO in its public example. This project uses native WebSocket via the `websockets` package as requested. If a DevKit instance exposes only Socket.IO, place a Socket.IO-to-native-WebSocket adapter in front of it or update the DevKit bridge transport while preserving the AutoDRIVE payload schema.

## License

This project is licensed under the BSD 3-Clause License. See `LICENSE`.
