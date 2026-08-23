from __future__ import annotations

import socket
import threading
from contextlib import suppress

from packages.protocol.codec import decode_envelope, encode_envelope
from packages.protocol.envelope import Envelope
from packages.protocol.errors import ProtocolError, ProtocolErrorCode
from packages.protocol.framing import receive_frame, send_frame
from packages.protocol.version import MAX_FRAME_SIZE


class FramedSocket:
    def __init__(self, connection: socket.socket, *, max_frame_size: int = MAX_FRAME_SIZE) -> None:
        self._connection = connection
        self._max_frame_size = max_frame_size
        self._send_lock = threading.Lock()

    def set_timeout(self, timeout_seconds: float | None) -> None:
        self._connection.settimeout(timeout_seconds)

    def send(self, envelope: Envelope) -> None:
        encoded = encode_envelope(envelope)
        with self._send_lock:
            try:
                send_frame(self._connection, encoded, max_frame_size=self._max_frame_size)
            except ProtocolError:
                raise
            except OSError as exc:
                raise ProtocolError(
                    ProtocolErrorCode.IPC_CONNECTION_LOST,
                    "connection failed while sending frame",
                ) from exc

    def receive(self) -> Envelope:
        payload = receive_frame(self._connection, max_frame_size=self._max_frame_size)
        return decode_envelope(payload)

    def close(self) -> None:
        with suppress(OSError):
            self._connection.shutdown(socket.SHUT_RDWR)
        self._connection.close()
