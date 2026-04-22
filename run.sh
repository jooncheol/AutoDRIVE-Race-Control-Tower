#!/usr/bin/env bash
# SPDX-License-Identifier: BSD-3-Clause
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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

docker_flags=(
  --rm
  --network host
  --volume "${SCRIPT_DIR}/frontend:/app/frontend:ro"
)
if [[ -t 0 && -t 1 ]]; then
  docker_flags+=(-it)
fi

debug_env_flags=()
if [[ -n "${RCT_DEBUG_ENGINEIO_MESSAGES+x}" ]]; then
  debug_env_flags+=(-e "RCT_DEBUG_ENGINEIO_MESSAGES=${RCT_DEBUG_ENGINEIO_MESSAGES}")
fi
if [[ -n "${RCT_DEBUG_ENGINEIO_MAX_CHARS+x}" ]]; then
  debug_env_flags+=(-e "RCT_DEBUG_ENGINEIO_MAX_CHARS=${RCT_DEBUG_ENGINEIO_MAX_CHARS}")
fi
if [[ -n "${RCT_DEBUG_SOCKETIO_CLIENT+x}" ]]; then
  debug_env_flags+=(-e "RCT_DEBUG_SOCKETIO_CLIENT=${RCT_DEBUG_SOCKETIO_CLIENT}")
fi
if [[ -n "${RCT_DEBUG_ENGINEIO_CLIENT+x}" ]]; then
  debug_env_flags+=(-e "RCT_DEBUG_ENGINEIO_CLIENT=${RCT_DEBUG_ENGINEIO_CLIENT}")
fi
if [[ -n "${RCT_DEBUG_SOCKETIO_SERVER+x}" ]]; then
  debug_env_flags+=(-e "RCT_DEBUG_SOCKETIO_SERVER=${RCT_DEBUG_SOCKETIO_SERVER}")
fi
if [[ -n "${RCT_DEBUG_ENGINEIO_SERVER+x}" ]]; then
  debug_env_flags+=(-e "RCT_DEBUG_ENGINEIO_SERVER=${RCT_DEBUG_ENGINEIO_SERVER}")
fi
if [[ -n "${RCT_DEBUG_BRIDGE_FLOW+x}" ]]; then
  debug_env_flags+=(-e "RCT_DEBUG_BRIDGE_FLOW=${RCT_DEBUG_BRIDGE_FLOW}")
fi
if [[ -n "${RCT_LOG_BRIDGE_MESSAGES+x}" ]]; then
  debug_env_flags+=(-e "RCT_LOG_BRIDGE_MESSAGES=${RCT_LOG_BRIDGE_MESSAGES}")
fi
if [[ -n "${RCT_LOG_BRIDGE_MAX_CHARS+x}" ]]; then
  debug_env_flags+=(-e "RCT_LOG_BRIDGE_MAX_CHARS=${RCT_LOG_BRIDGE_MAX_CHARS}")
fi
if [[ -n "${RCT_LOG_BRIDGE_FIELD_SIZES+x}" ]]; then
  debug_env_flags+=(-e "RCT_LOG_BRIDGE_FIELD_SIZES=${RCT_LOG_BRIDGE_FIELD_SIZES}")
fi
if [[ -n "${RCT_EMPTY_FRONT_CAMERA_IN_BRIDGE_HISTORY+x}" ]]; then
  debug_env_flags+=(-e "RCT_EMPTY_FRONT_CAMERA_IN_BRIDGE_HISTORY=${RCT_EMPTY_FRONT_CAMERA_IN_BRIDGE_HISTORY}")
fi
if [[ -n "${RCT_REPLACE_FRONT_CAMERA_WITH_WHITE_JPEG+x}" ]]; then
  debug_env_flags+=(-e "RCT_REPLACE_FRONT_CAMERA_WITH_WHITE_JPEG=${RCT_REPLACE_FRONT_CAMERA_WITH_WHITE_JPEG}")
fi
if [[ -n "${RCT_ENABLE_ORIGIN+x}" ]]; then
  debug_env_flags+=(-e "RCT_ENABLE_ORIGIN=${RCT_ENABLE_ORIGIN}")
fi

docker run "${docker_flags[@]}" \
  -e "RCT_PORT=${RCT_PORT}" \
  -e "RCT_DEVKIT_URLS=${RCT_DEVKIT_URLS}" \
  -e "RCT_DEVKIT_VEHICLE_IDS=${RCT_DEVKIT_VEHICLE_IDS}" \
  "${debug_env_flags[@]}" \
  "${IMAGE_NAME}"
