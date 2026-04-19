# AutoDRIVE RCT Monitor Protocol

Version: `0.1`

AutoDRIVE RCT Monitor Protocol defines communication between the browser frontend and the RCT server. Its primary purpose is to let the web frontend monitor the simulator, Roboracer DevKit instances, and the bridge traffic relayed by RCT.

## Path Layout

The protocol uses this path layout:

```text
/monitor/{transport}/{version}
```

Current endpoints:

```text
/monitor/REST/latest
/monitor/WS/latest
/monitor/REST/0.1
/monitor/WS/0.1
```

`latest` is an alias for the current protocol version, `0.1`.

The transport-before-version layout is intentional. Monitor clients first choose how they communicate with RCT, then choose either an explicit version or the `latest` alias. This keeps the REST and WebSocket surfaces parallel and avoids mixing static frontend paths with API paths.

## Transports

- `REST`: HTTP GET/POST commands and snapshots served by `aiohttp`
- `WS`: plain WebSocket monitor event stream served by `aiohttp`

The monitor WebSocket is separate from the simulator/DevKit Socket.IO bridge. Browser monitoring does not need Socket.IO.

## Shared State Model

RCT keeps monitorable state in one in-process `RaceControlState` object. REST handlers read snapshots from this object, and WebSocket handlers publish events derived from the same object. This keeps GET responses and WS events consistent.

Monitor WebSocket delivery is handled by `MonitorEventHub`. State mutation and network fanout are intentionally separate:

1. Update `RaceControlState`.
2. Build a snapshot or event payload.
3. Broadcast through `MonitorEventHub`.

Network I/O must not happen while mutating shared state. This prevents slow monitor clients from blocking simulator, DevKit, or REST state updates.

### REST

REST endpoints are used to query RCT server state and, later, send control commands.

Implemented in `0.1`:

```http
GET /monitor/REST/0.1
GET /monitor/REST/latest
```

Response shape:

```json
{
  "protocol": "autodrive-rct-monitor",
  "transport": "REST",
  "requested_version": "latest",
  "version": "0.1",
  "latest": "0.1",
  "aliases": {
    "latest": "/monitor/REST/latest",
    "versioned": "/monitor/REST/0.1",
    "events": "/monitor/WS/latest"
  },
  "state": {
    "monitor_protocol": {
      "name": "autodrive-rct-monitor",
      "version": "0.1"
    },
    "simulator_clients": 0,
    "monitor_clients": 1,
    "devkits": []
  }
}
```

REST command surface:

```http
POST /monitor/REST/0.1/devkits/{vehicle_id}/connect
POST /monitor/REST/0.1/devkits/{vehicle_id}/disconnect
```

These commands start or stop the Socket.IO client session from RCT to the selected DevKit bridge instance. Normal operation starts DevKit sessions automatically when the simulator connects.

### WebSocket

WebSocket endpoints are used for live monitor events.

```text
ws://<rct-host>:<rct-port>/monitor/WS/0.1
ws://<rct-host>:<rct-port>/monitor/WS/latest
```

Initial event:

```json
{
  "event": "status",
  "timestamp": "2026-04-19T00:00:00+00:00",
  "monitor_protocol": {
    "name": "autodrive-rct-monitor",
    "version": "0.1"
  },
  "simulator_clients": 0,
  "monitor_clients": 1,
  "devkits": []
}
```

Existing frame event:

```json
{
  "event": "frame",
  "timestamp": "2026-04-19T00:00:00+00:00",
  "source": "simulator",
  "targets": [
    {
      "name": "devkit:1",
      "vehicle_id": 1
    }
  ],
  "socketio_event": "Bridge",
  "args": [
    {
      "encoding": "json",
      "payload": {}
    }
  ]
}
```

Planned live monitor event categories:

- `status`: RCT server, simulator, monitor client, and DevKit connection state
- `vehicle_state`: per-Roboracer IPS and vehicle telemetry
- `lap_time`: lap count, this lap, last lap, best lap, and total penalty
- `collision`: collision count updates
- `bridge_rate`: bridge protocol Hz between simulator and each Roboracer DevKit instance
- `frame`: raw or encoded bridge protocol observation event
- `error`: monitor protocol or command error

Monitor WebSocket command surface:

```json
{
  "command": "configure-devkits",
  "devkits": [
    {
      "vehicle_id": 1,
      "host": "127.0.0.1",
      "port": 4568
    },
    {
      "vehicle_id": 2,
      "host": "127.0.0.1",
      "port": 4569
    }
  ]
}
```

```json
{
  "command": "connect-devkit",
  "vehicle_id": 1,
  "host": "127.0.0.1",
  "port": 4568
}
```

```json
{
  "command": "disconnect-devkit",
  "vehicle_id": 1
}
```

The frontend sends `configure-devkits` when it connects to RCT. RCT stores those endpoints and does not connect DevKit bridge instances from startup defaults. When the simulator connects, RCT connects only configured and enabled DevKit bridge instances. The connected/disconnected frontend buttons send `connect-devkit` and `disconnect-devkit` commands for manual control.

## Bridge Proxy Cache

RCT keeps two Bridge caches:

- Incoming cache: latest `Bridge` payload received from the simulator.
- Outgoing cache: latest command payload sent to the simulator.

The outgoing cache starts with:

```json
{
  "V1 Reset": "False",
  "V1 Throttle": "0.0",
  "V1 Steering": "0.0",
  "V2 Reset": "False",
  "V2 Throttle": "0.0",
  "V2 Steering": "0.0"
}
```

When a DevKit sends a `Bridge` event, RCT rewrites that DevKit's id `1` fields back to the assigned simulator vehicle id, merges the changed control fields into the outgoing cache, then emits the complete outgoing cache to the simulator. This ensures the simulator always receives V1 and V2 reset/throttle/steering fields together.

RCT also tracks which DevKit caused each outgoing simulator `Bridge` message. The next simulator `Bridge` response is stored in the incoming cache and forwarded to that DevKit after rewriting the assigned simulator vehicle id back to DevKit id `1`. If no pending DevKit response target exists, simulator `Bridge` data is forwarded to all configured and enabled DevKit bridge instances.

Each DevKit state includes a rolling 60-second completed-cycle Bridge rate:

- `bridge_hz`
- `bridge_per_minute`

The rate is recorded when a simulator `Bridge` response is routed back to the DevKit that caused the outgoing simulator command.

## Non-Monitor Paths

Simulator Socket.IO endpoint:

```text
ws://<rct-host>:<rct-port>/socket.io/?EIO=4&transport=websocket
```

Frontend static files:

```text
http://<rct-host>:<rct-port>/
```

RCT uses `aiohttp` for static files and monitor REST/WS routes, and `python-socketio` for simulator and DevKit bridge sessions. The simulator and DevKit bridge payloads are still the AutoDRIVE bridge payloads; Socket.IO is the transport and event envelope.

## Proxy Transport

The simulator connects to RCT as a Socket.IO client. RCT then connects to each configured DevKit bridge as a Socket.IO client using the WebSocket transport.

DevKit URLs are configured through `RCT_DEVKIT_URLS`. RCT accepts both WebSocket-looking URLs and HTTP-looking URLs:

```text
ws://127.0.0.1:4568
http://127.0.0.1:4568
```

`python-socketio` connects with an HTTP(S) base URL and a Socket.IO path. RCT normalizes `ws://` to `http://` and `wss://` to `https://`, then connects with:

```text
socketio_path=socket.io
transports=["websocket"]
```

Payload rewriting still follows the RCT bridge rules:

- Simulator to DevKit: assigned simulator id becomes id `1`.
- DevKit to Simulator: id `1` becomes the assigned simulator id.
- Dict/list payloads keep their Python shape.
- Text payloads keep their text shape.
- Binary payloads are forwarded without id rewriting.
