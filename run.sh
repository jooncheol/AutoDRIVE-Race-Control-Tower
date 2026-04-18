#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-autodrive-rct:dev}"
RCT_BUILD_IMAGE="${RCT_BUILD_IMAGE:-1}"
RCT_PORT="${RCT_PORT:-4567}"
if [[ -z "${RCT_DEVKIT_URLS+x}" ]]; then
  RCT_DEVKIT_URLS="ws://127.0.0.1:4568,ws://127.0.0.1:4569"
fi
if [[ -z "${RCT_DEVKIT_VEHICLE_IDS+x}" ]]; then
  if [[ -z "${RCT_DEVKIT_URLS}" ]]; then
    RCT_DEVKIT_VEHICLE_IDS=""
  else
    RCT_DEVKIT_VEHICLE_IDS="1,2"
  fi
fi

if [[ "${RCT_BUILD_IMAGE}" == "1" ]]; then
  docker build -t "${IMAGE_NAME}" .
fi

docker_flags=(--rm --network host)
if [[ -t 0 && -t 1 ]]; then
  docker_flags+=(-it)
fi

docker run "${docker_flags[@]}" \
  -e "RCT_PORT=${RCT_PORT}" \
  -e "RCT_DEVKIT_URLS=${RCT_DEVKIT_URLS}" \
  -e "RCT_DEVKIT_VEHICLE_IDS=${RCT_DEVKIT_VEHICLE_IDS}" \
  "${IMAGE_NAME}"
