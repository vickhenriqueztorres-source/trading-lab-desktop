from __future__ import annotations

import socket
import struct

from packages.protocol.errors import ProtocolError, ProtocolErrorCode
from packages.protocol.version import MAX_FRAME_SIZE

_HEADER = struct.Struct("!I")


def frame_payload(payload: bytes, *, max_frame_size: int = MAX_FRAME_SIZE) -> bytes:
    size = len(payload)
    if size == 0:
        raise ProtocolError(ProtocolErrorCode.IPC_INVALID_FRAME, "empty frames are forbidden")
    if size > max_frame_size:
        raise ProtocolError(
            ProtocolErrorCode.IPC_FRAME_TOO_LARGE,
            "frame exceeds configured maximum",
        )
    return _HEADER.pack(size) + payload


def _receive_exact(connection: socket.socket, size: int, *, header: bool = False) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        try:
            chunk = connection.recv(remaining)
        except TimeoutError as exc:
            raise ProtocolError(
                ProtocolErrorCode.IPC_FRAME_TRUNCATED,
                "frame timed out before completion",
            ) from exc
        except OSError as exc:
            raise ProtocolError(
                ProtocolErrorCode.IPC_CONNECTION_LOST,
                "connection failed while receiving frame",
            ) from exc
        if not chunk:
            code = (
                ProtocolErrorCode.IPC_CONNECTION_LOST
                if header and not chunks
                else ProtocolErrorCode.IPC_FRAME_TRUNCATED
            )
            raise ProtocolError(code, "connection closed before frame completion")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def receive_frame(
    connection: socket.socket,
    *,
    max_frame_size: int = MAX_FRAME_SIZE,
) -> bytes:
    header = _receive_exact(connection, _HEADER.size, header=True)
    (announced_size,) = _HEADER.unpack(header)
    if announced_size == 0:
        raise ProtocolError(ProtocolErrorCode.IPC_INVALID_FRAME, "empty frames are forbidden")
    if announced_size > max_frame_size:
        raise ProtocolError(
            ProtocolErrorCode.IPC_FRAME_TOO_LARGE,
            "announced frame exceeds configured maximum",
        )
    return _receive_exact(connection, announced_size)


def send_frame(
    connection: socket.socket,
    payload: bytes,
    *,
    max_frame_size: int = MAX_FRAME_SIZE,
) -> None:
    connection.sendall(frame_payload(payload, max_frame_size=max_frame_size))
