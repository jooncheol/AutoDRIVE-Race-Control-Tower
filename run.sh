#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-autodrive-rct:dev}"
RCT_PORT="${RCT_PORT:-8765}"
RCT_DEVKIT_URLS="${RCT_DEVKIT_URLS:-ws://host.docker.internal:4567,ws://host.docker.internal:4568}"
RCT_DEVKIT_VEHICLE_IDS="${RCT_DEVKIT_VEHICLE_IDS:-1,2}"

docker run --rm -it \
  -p "${RCT_PORT}:8765" \
  -e RCT_PORT=8765 \
  -e "RCT_DEVKIT_URLS=${RCT_DEVKIT_URLS}" \
  -e "RCT_DEVKIT_VEHICLE_IDS=${RCT_DEVKIT_VEHICLE_IDS}" \
  "${IMAGE_NAME}"
