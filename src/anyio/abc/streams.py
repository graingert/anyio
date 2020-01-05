import sys
from abc import abstractmethod
from typing import Generic, TypeVar, Union, Optional, Tuple, overload, Dict, List

from . import AsyncResource
from ..exceptions import ClosedResourceError

if sys.version_info >= (3, 8):
    from typing import Literal
else:
    from typing_extensions import Literal

T_Item = TypeVar('T_Item')
T_Stream = TypeVar('T_Stream', bound='AnyByteStream', covariant=True)


class UnreliableReceiveMessageStream(Generic[T_Item], AsyncResource):
    """
    An interface for receiving objects.

    This interface makes no guarantees that the received messages arrive in the order in which they
    were sent, or that no messages are missed.

    Asynchronously iterating over objects of this type will yield objects matching the given type
    parameter.
    """

    def __aiter__(self):
        return self

    async def __anext__(self) -> T_Item:
        try:
            return await self.receive()
        except ClosedResourceError:
            raise StopAsyncIteration

    @abstractmethod
    async def receive(self) -> T_Item:
        """Receive the next item."""


class UnreliableSendMessageStream(Generic[T_Item], AsyncResource):
    """
    An interface for sending objects.

    This interface makes no guarantees that the messages sent will reach the recipient(s) in the
    same order in which they were sent, or at all.
    """

    @abstractmethod
    async def send(self, item: T_Item) -> None:
        """
        Send an item to the peer(s).

        :param item: the item to send
        """


class UnreliableMessageStream(Generic[T_Item], UnreliableReceiveMessageStream[T_Item],
                              UnreliableSendMessageStream[T_Item]):
    """
    A bidirectional message stream which does not guarantee the order or reliability of message
    delivery.
    """


class ReceiveMessageStream(Generic[T_Item], UnreliableReceiveMessageStream[T_Item]):
    """
    A receive message stream which guarantees that messages are received in the same order in
    which they were sent, and that no messages are missed.
    """


class SendMessageStream(Generic[T_Item], UnreliableSendMessageStream[T_Item]):
    """
    A send message stream which guarantees that messages are delivered in the same order in which
    they were sent, without missing any messages in the middle.
    """


class MessageStream(Generic[T_Item], ReceiveMessageStream[T_Item], SendMessageStream[T_Item],
                    UnreliableMessageStream[T_Item]):
    """
    A bidirectional message stream which guarantees the order and reliability of message delivery.
    """


class ReceiveByteStream(AsyncResource):
    """
    An interface for receiving bytes from a single peer.

    Iterating this byte stream will yield a byte string of arbitrary length, but no more than
    65536 bytes.
    """

    def __aiter__(self) -> 'ReceiveByteStream':
        return self

    async def __anext__(self) -> bytes:
        data = await self.receive(65536)
        if data:
            return data
        else:
            raise StopAsyncIteration

    @abstractmethod
    async def receive(self, max_bytes: Optional[int] = None) -> bytes:
        """
        Receive at most ``max_bytes`` bytes from the peer.

        :param max_bytes:
        :return: the received bytes
        """


class SendByteStream(AsyncResource):
    """An interface for sending bytes to a single peer."""

    @abstractmethod
    async def send(self, item: bytes) -> None:
        """
        Send the given bytes to the peer.

        :param item: the bytes to send
        """


class ByteStream(ReceiveByteStream, SendByteStream):
    """A bidirectional byte stream."""


AnyUnreliableReceiveByteStream = Union[UnreliableReceiveMessageStream[bytes], ReceiveByteStream]
AnyUnreliableSendByteStream = Union[UnreliableSendMessageStream[bytes], SendByteStream]
AnyUnreliableByteStream = Union[UnreliableMessageStream[bytes], ByteStream]
AnyReceiveByteStream = Union[ReceiveMessageStream[bytes], ReceiveByteStream]
AnySendByteStream = Union[SendMessageStream[bytes], SendByteStream]
AnyByteStream = Union[MessageStream[bytes], ByteStream]


class TLSByteStream(ByteStream):
    @abstractmethod
    async def unwrap(self) -> Tuple[AnyByteStream, bytes]:
        """
        Do the closing handshake and return any bytes still left in the read buffer.

        :return: a tuple of (previously wrapped stream, bytes left in the read buffer)
        """

    @property
    @abstractmethod
    def alpn_protocol(self) -> Optional[str]:
        """
        The ALPN protocol selected during the TLS handshake.

        :return: The selected ALPN protocol, or ``None`` if no ALPN protocol was selected
        """

    @abstractmethod
    def get_channel_binding(self, cb_type: str = 'tls-unique') -> bytes:
        """
        Get the channel binding data for the current connection.

        See :func:`ssl.SSLSocket.get_channel_binding` for more information.

        :param cb_type: type of the channel binding to get
        :return: the channel binding data
        """

    @property
    @abstractmethod
    def tls_version(self) -> str:
        """
        The TLS version negotiated during the TLS handshake.

        See :func:`ssl.SSLSocket.version` for more information.

        :return: the TLS version string (e.g. "TLSv1.3")
        """

    @property
    @abstractmethod
    def cipher(self) -> Tuple[str, str, int]:
        """
        The cipher selected in the TLS handshake.

        See :func:`ssl.SSLSocket.cipher` for more information.

        :return: a 3-tuple of (cipher name, TLS version which defined it, number of bits)
        """

    @property
    @abstractmethod
    def shared_ciphers(self) -> List[Tuple[str, str, int]]:
        """
        The list of ciphers supported by both parties in the TLS handshake.

        See :func:`ssl.SSLSocket.shared_ciphers` for more information.

        :return: a list of 3-tuples (cipher name, TLS version which defined it, number of bits)
        """

    @property
    @abstractmethod
    def server_hostname(self) -> Optional[str]:
        """
        The server host name.

        :return: the server host name, or ``None`` if this is the server side of the connection
        """

    @property
    @abstractmethod
    def server_side(self) -> bool:
        """
        ``True`` if this is the server side of the connection, ``False`` if this is the client.

        :return: ``True`` if this is the server side, ``False`` if this is the client side
        """

    @overload
    def getpeercert(self, binary_form: Literal[False] = False) -> Dict[str, Union[str, tuple]]:
        ...

    @overload
    def getpeercert(self, binary_form: Literal[True]) -> bytes:
        ...

    @abstractmethod
    def getpeercert(self, binary_form: bool = False) -> Union[Dict[str, Union[str, tuple]], bytes,
                                                              None]:
        """
        Get the certificate for the peer on the other end of the connection.

        See :func:`ssl.SSLSocket.getpeercert` for more information.

        :param binary_form: ``False`` to return the certificate as a dict, ``True`` to return it
            as bytes
        :return: the peer's certificate, or ``None`` if the peer did not provide a certificate
        """


class Listener(Generic[T_Stream], AsyncResource):
    """
    An interface for objects that let you accept incoming connections.

    Asynchronously iterating over this object will yield streams matching the type parameter
    given for this interface.
    """

    def __aiter__(self):
        return self

    async def __anext__(self) -> T_Stream:
        try:
            return await self.accept()
        except ClosedResourceError:
            raise StopAsyncIteration

    @abstractmethod
    async def accept(self) -> T_Stream:
        """Accept an incoming connection."""


class Endpoint(Generic[T_Stream], AsyncResource):
    @property
    @abstractmethod
    def connected(self) -> bool:
        """Return ``True`` if this connector is currently connected."""

    async def ensure_connected(self) -> T_Stream:
        """
        Ensure that a connection to the designated endpoint is available.

        :return: a connected stream
        """
