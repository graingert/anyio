import ssl
import sys
from typing import AsyncIterable, Optional, Dict, Union, Callable, TypeVar, Tuple, List, overload

import attr

from .abc import AsyncResource
from .abc.streams import (
    MessageStream, AnyReceiveByteStream, ReceiveMessageStream, SendMessageStream,
    AnySendByteStream, AnyByteStream, TLSByteStream)
from .exceptions import DelimiterNotFound, IncompleteRead, ClosedResourceError

if sys.version_info >= (3, 8):
    from typing import Literal
else:
    from typing_extensions import Literal

T_Retval = TypeVar('T_Retval')


@attr.s(slots=True, auto_attribs=True)
class BufferedByteReader(AsyncResource):
    stream: AnyReceiveByteStream
    _buffer: bytes = attr.ib(init=False, default=b'')

    async def aclose(self) -> None:
        await self.stream.aclose()

    @property
    def buffer(self) -> bytes:
        return self._buffer

    async def receive_exactly(self, nbytes: int) -> bytes:
        """
        Read exactly the given amount of bytes from the stream.

        :param nbytes: the number of bytes to read
        :return: the bytes read
        :raises anyio.exceptions.IncompleteRead: if the stream was closed before the requested
            amount of bytes could be read from the stream

        """
        bytes_left = nbytes - len(self._buffer)
        while bytes_left > 0:
            chunk = await self.stream.receive()
            if not chunk:
                raise IncompleteRead

            self._buffer += chunk
            bytes_left -= len(chunk)

        result = self._buffer[:nbytes]
        self._buffer = self._buffer[nbytes:]
        return result

    async def receive_until(self, delimiter: bytes, max_bytes: int) -> bytes:
        """
        Read from the stream until the delimiter is found or max_bytes have been read.

        :param delimiter: the marker to look for in the stream
        :param max_bytes: maximum number of bytes that will be read before raising
            :exc:`~anyio.exceptions.DelimiterNotFound`
        :return: the bytes read, including the delimiter
        :raises anyio.exceptions.IncompleteRead: if the stream was closed before the delimiter
            was found
        :raises anyio.exceptions.DelimiterNotFound: if the delimiter is not found within the
            bytes read up to the maximum allowed

        """
        delimiter_size = len(delimiter)
        offset = 0
        while True:
            # Check if the delimiter can be found in the current buffer
            index = self._buffer.find(delimiter, offset)
            if index >= 0:
                found = self._buffer[:index]
                self._buffer = self._buffer[index + len(delimiter):]
                return found

            # Check if the buffer is already at or over the limit
            if len(self._buffer) >= max_bytes:
                raise DelimiterNotFound(max_bytes)

            # Read more data into the buffer from the socket
            data = await self.stream.receive()
            if not data:
                raise IncompleteRead

            # Move the offset forward and add the new data to the buffer
            offset = max(len(self._buffer) - delimiter_size + 1, 0)
            self._buffer += data

    async def receive_delimited_chunks(self, delimiter: bytes,
                                       max_chunk_size: int) -> AsyncIterable[bytes]:
        """
        Return an async iterable which yields chunks of bytes as soon as they are received.

        The generator will yield new chunks until the stream is closed.

        :param delimiter: the marker to look for in the stream
        :param max_chunk_size: maximum number of bytes that will be read for each chunk before
            raising :exc:`~anyio.exceptions.DelimiterNotFound`
        :return: an async iterable yielding bytes
        :raises anyio.exceptions.IncompleteRead: if the stream was closed before the delimiter
            was found
        """
        while True:
            try:
                chunk = await self.receive_until(delimiter, max_chunk_size)
            except IncompleteRead:
                if self._buffer:
                    raise
                else:
                    break

            yield chunk


class ReceiveTextStream(ReceiveMessageStream[str]):
    __slots__ = ('buffer', 'delimiter', 'encoding', 'errors', 'fallback_encoding',
                 'max_chunk_size')

    def __init__(self, stream: AnyReceiveByteStream, delimiter: str, encoding: str = 'utf-8',
                 errors: str = 'strict', fallback_encoding: Optional[str] = None,
                 max_chunk_size: int = 2**16):
        self.buffer = BufferedByteReader(stream)
        self.delimiter = delimiter.encode(encoding)
        self.encoding = encoding
        self.errors = errors
        self.fallback_encoding = fallback_encoding
        self.max_chunk_size = max_chunk_size

    async def aclose(self) -> None:
        await self.buffer.stream.aclose()

    async def receive(self) -> str:
        line = await self.buffer.receive_until(self.delimiter, self.max_chunk_size)
        try:
            return line.decode(self.encoding, errors=self.errors)
        except UnicodeDecodeError:
            if self.fallback_encoding:
                return line.decode(self.fallback_encoding)
            else:
                raise


class SendTextStream(SendMessageStream[str]):
    def __init__(self, stream: AnySendByteStream, delimiter: str = '', encoding: str = 'utf-8',
                 errors: str = 'strict', fallback_encoding: Optional[str] = None):
        self.stream = stream
        self.delimiter = delimiter.encode(encoding)
        self.encoding = encoding
        self.errors = errors
        self.fallback_encoding = fallback_encoding

    async def aclose(self) -> None:
        await self.stream.aclose()

    async def send(self, item: str) -> None:
        try:
            data = item.encode(self.encoding)
        except UnicodeEncodeError:
            if self.fallback_encoding:
                data = item.encode(self.fallback_encoding)
            else:
                raise

        await self.stream.send(data + self.delimiter)


class TextStream(MessageStream[str]):
    __slots__ = ('_stream', 'receive_stream', 'send_stream')

    def __init__(self, stream: AnyByteStream, delimiter: str, encoding: str = 'utf-8',
                 errors: str = 'strict', fallback_encoding: Optional[str] = None,
                 max_chunk_size: int = 2**16):
        self._stream = stream
        self.receive_stream = ReceiveTextStream(stream, delimiter, encoding, errors,
                                                fallback_encoding, max_chunk_size)
        self.send_stream = SendTextStream(stream, delimiter, encoding, errors, fallback_encoding)

    async def receive(self) -> str:
        return await self.receive_stream.receive()

    async def send(self, item: str) -> None:
        await self.send_stream.send(item)

    async def aclose(self) -> None:
        await self._stream.aclose()


@attr.s(slots=True, auto_attribs=True, frozen=True)
class TLSWrapperStream(TLSByteStream):
    _stream: AnyByteStream
    _ssl_object: ssl.SSLObject
    _read_bio: ssl.MemoryBIO
    _write_bio: ssl.MemoryBIO
    _tls_standard_compatible: bool

    @classmethod
    async def wrap(cls, stream: AnyByteStream, server_hostname: Optional[str] = None, *,
                   context: Optional[ssl.SSLContext] = None,
                   tls_standard_compatible: bool = True) -> 'TLSWrapperStream':
        """
        Wrap an existing stream with Transport Layer Security.

        This performs a TLS handshake with the peer.

        :param stream: the underlying stream to wrap
        :param server_hostname: host name of the server, if acting as a client
        :param context: the SSLContext object to use (if not provided, a secure default will be
            used)
        :param tls_standard_compatible: if ``False``, skip the closing handshake when closing the
            connection, and don't raise an exception if the peer does the same

        """
        if not context:
            purpose = ssl.Purpose.SERVER_AUTH if server_hostname else ssl.Purpose.CLIENT_AUTH
            context = ssl.create_default_context(purpose)

        bio_in = ssl.MemoryBIO()
        bio_out = ssl.MemoryBIO()
        ssl_object = context.wrap_bio(bio_in, bio_out, server_side=not server_hostname,
                                      server_hostname=server_hostname)
        wrapper = cls(stream=stream, ssl_object=ssl_object, read_bio=bio_in, write_bio=bio_out,
                      tls_standard_compatible=tls_standard_compatible)
        await wrapper._call_sslobject_method(ssl_object.do_handshake)
        return wrapper

    async def _call_sslobject_method(self, func: Callable[..., T_Retval], *args) -> T_Retval:
        while True:
            try:
                result = func(*args)
            except ssl.SSLWantReadError:
                # Flush any pending writes first
                if self._write_bio.pending:
                    await self._stream.send(self._write_bio.read())

                try:
                    data = await self._stream.receive()
                except ClosedResourceError:
                    data = b''

                if data:
                    self._read_bio.write(data)
                else:
                    self._read_bio.write_eof()
                    self._write_bio.write_eof()
            except ssl.SSLWantWriteError:
                await self._stream.send(self._write_bio.read())
            else:
                # Flush any pending writes first
                if self._write_bio.pending:
                    await self._stream.send(self._write_bio.read())

                return result

    async def unwrap(self) -> Tuple[AnyByteStream, bytes]:
        await self._call_sslobject_method(self._ssl_object.unwrap)
        self._read_bio.write_eof()
        self._write_bio.write_eof()
        return self._stream, self._read_bio.read()

    async def aclose(self) -> None:
        try:
            if self._tls_standard_compatible:
                await self.unwrap()
        finally:
            await self._stream.aclose()

    async def receive(self, max_bytes: Optional[int] = None) -> bytes:
        return await self._call_sslobject_method(self._ssl_object.read, max_bytes or 65536)

    async def send(self, item: bytes) -> None:
        await self._call_sslobject_method(self._ssl_object.write, item)

    @property
    def alpn_protocol(self) -> Optional[str]:
        return self._ssl_object.selected_alpn_protocol()  # type: ignore

    def get_channel_binding(self, cb_type: str = 'tls-unique') -> bytes:
        return self._ssl_object.get_channel_binding(cb_type)  # type: ignore

    @property
    def tls_version(self) -> str:
        return self._ssl_object.version()  # type: ignore

    @property
    def cipher(self) -> Tuple[str, str, int]:
        return self._ssl_object.cipher()  # type: ignore

    @property
    def shared_ciphers(self) -> List[Tuple[str, str, int]]:
        return self._ssl_object.shared_ciphers()  # type: ignore

    @property
    def server_hostname(self) -> Optional[str]:
        return self._ssl_object.server_hostname

    @property
    def server_side(self) -> bool:
        return self._ssl_object.server_side

    @overload
    def getpeercert(self, binary_form: Literal[False] = False) -> Dict[str, Union[str, tuple]]:
        ...

    @overload
    def getpeercert(self, binary_form: Literal[True]) -> bytes:
        ...

    def getpeercert(self, binary_form: bool = False) -> Union[Dict[str, Union[str, tuple]], bytes,
                                                              None]:
        return self._ssl_object.getpeercert(binary_form)
