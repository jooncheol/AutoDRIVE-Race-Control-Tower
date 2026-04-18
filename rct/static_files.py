# SPDX-License-Identifier: BSD-3-Clause

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from urllib.parse import unquote, urlparse


@dataclass(frozen=True)
class StaticFileResponse:
    status_code: int
    reason_phrase: str
    headers: tuple[tuple[str, str], ...]
    body: bytes


def build_static_file_response(request_path: str, static_root: Path) -> StaticFileResponse:
    parsed = urlparse(request_path)
    relative_path = _relative_static_path(unquote(parsed.path))
    static_root = static_root.resolve()
    candidate = (static_root / relative_path).resolve()

    try:
        candidate.relative_to(static_root)
    except ValueError:
        return _not_found()

    if not candidate.is_file():
        return _not_found()

    body = candidate.read_bytes()
    content_type = _content_type(candidate)
    return StaticFileResponse(
        status_code=HTTPStatus.OK.value,
        reason_phrase=HTTPStatus.OK.phrase,
        headers=(
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("X-Content-Type-Options", "nosniff"),
        ),
        body=body,
    )


def _relative_static_path(path: str) -> str:
    normalized_path = path.rstrip("/") or "/"
    if normalized_path in {"/", "/index.html"}:
        return "index.html"
    return normalized_path.lstrip("/")


def _content_type(path: Path) -> str:
    guessed_type, _ = mimetypes.guess_type(path.name)
    if guessed_type is None:
        return "application/octet-stream"
    if guessed_type.startswith("text/") or guessed_type in {
        "application/javascript",
        "application/json",
        "application/xml",
    }:
        return f"{guessed_type}; charset=utf-8"
    return guessed_type


def _not_found() -> StaticFileResponse:
    body = b"Not Found\n"
    return StaticFileResponse(
        status_code=HTTPStatus.NOT_FOUND.value,
        reason_phrase=HTTPStatus.NOT_FOUND.phrase,
        headers=(
            ("Content-Type", "text/plain; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("X-Content-Type-Options", "nosniff"),
        ),
        body=body,
    )
