from __future__ import annotations

import socket

from .._core._eventloop import get_async_backend
from .._core._exceptions import BrokenResourceError, ClosedResourceError, EndOfStream
from .._core._synchronization import ResourceGuard
from ..abc import SocketAttribute, SocketStream


class RawSocketStream(SocketStream):
    def __init__(self, raw_socket: socket.socket):
        self.__raw_socket = raw_socket
        self._closed = False
        self._receive_guard = ResourceGuard("reading from")
        self._send_guard = ResourceGuard("writing to")

    @property
    def _raw_socket(self) -> socket.socket:
        return self.__raw_socket

    async def receive(self, max_bytes: int = 65536) -> bytes:
        if max_bytes < 1:
            raise ValueError("max_bytes must be a positive integer")

        with self._receive_guard:
            while True:
                if self._closed:
                    raise ClosedResourceError

                try:
                    data = self.__raw_socket.recv(max_bytes)
                except BlockingIOError:
                    await get_async_backend().wait_readable(self.__raw_socket)
                except OSError as exc:
                    if self._closed:
                        raise ClosedResourceError from None
                    raise BrokenResourceError from exc
                else:
                    if not data:
                        raise EndOfStream
                    return data

    async def send(self, item: bytes) -> None:
        with self._send_guard:
            view = memoryview(item)
            while view:
                if self._closed:
                    raise ClosedResourceError

                try:
                    sent = self.__raw_socket.send(view)
                except BlockingIOError:
                    await get_async_backend().wait_writable(self.__raw_socket)
                except OSError as exc:
                    if self._closed:
                        raise ClosedResourceError from None
                    raise BrokenResourceError from exc
                else:
                    view = view[sent:]

    async def send_eof(self) -> None:
        if self._closed:
            raise ClosedResourceError
        self.__raw_socket.shutdown(socket.SHUT_WR)

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            self.__raw_socket.close()


async def take_socket(stream: SocketStream) -> socket.socket:
    """Transfer a connected TCP socket out of an AnyIO backend stream."""
    raw_socket = stream.extra(SocketAttribute.raw_socket).dup()
    raw_socket.setblocking(False)

    transport = getattr(stream, "_transport", None)
    protocol = getattr(stream, "_protocol", None)
    if transport is not None and protocol is not None:
        transport.pause_reading()
        if protocol.read_queue:
            raw_socket.close()
            raise RuntimeError(
                "cannot enable kTLS after the asyncio transport has consumed data"
            )

        stream._closed = True  # type: ignore[attr-defined]
        transport.abort()
        await get_async_backend().cancel_shielded_checkpoint()
        return raw_socket

    trio_socket = getattr(stream, "_trio_socket", None)
    if trio_socket is not None:
        stream._closed = True  # type: ignore[attr-defined]
        trio_socket.close()
        return raw_socket

    raw_socket.close()
    raise NotImplementedError(
        "kTLS is only supported when wrapping an AnyIO TCP SocketStream"
    )
