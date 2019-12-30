import socket
from ipaddress import IPv4Address, IPv6Address
from typing import Tuple, Union, NamedTuple, Generic, TypeVar, overload

from .streams import ByteStream, UnreliableMessageStream, Listener

T_Address = TypeVar('T_Address')
T_SocketStream = TypeVar('T_SocketStream')
IPAddressType = Union[str, IPv4Address, IPv6Address]
RawIPPort = Union[Tuple[str, int], Tuple[str, int, int, int]]


class _SocketWrapper(Generic[T_Address]):
    raw_socket: socket.SocketType  #: the raw socket object wrapped by this stream
    # @property
    # @abstractmethod
    # def raw_socket(self) -> socket.SocketType:
    #     """The raw socket object wrapped by this stream."""

    @property
    def local_address(self) -> T_Address:
        """The local address the socket is bound to."""
        return self.raw_socket.getsockname()

    @overload
    def getsockopt(self, level: int, optname: int):
        ...

    @overload
    def getsockopt(self, level: int, optname: int, buflen: int):
        ...

    def getsockopt(self, level: int, optname: int, *args, **kwargs):
        return self.raw_socket.getsockopt(level, optname, *args, **kwargs)

    @overload
    def setsockopt(self, level: int, optname: int, value: Union[int, bytes]) -> None:
        ...

    @overload
    def setsockopt(self, level: int, optname: int, value: None, optlen: int) -> None:
        ...

    def setsockopt(self, level, optname, *args):
        self.raw_socket.setsockopt(level, optname, *args)


class _ConnectedSocketWrapper(Generic[T_Address], _SocketWrapper[T_Address]):
    @property
    def remote_address(self) -> RawIPPort:
        """The remote address of the peer."""
        return self.raw_socket.getpeername()


class TCPSocketStream(ByteStream, _ConnectedSocketWrapper[RawIPPort]):
    """A reliable stream backed by a TCP socket."""


class UNIXSocketStream(ByteStream, _ConnectedSocketWrapper[str]):
    """A reliable stream backed by a UNIX domain socket."""


class UDPPacket(NamedTuple):
    """Represents a UDP packet."""

    data: bytes  #: payload of the package
    #: a tuple of (IP address, port) for IPv4, or (IP address, port, flowinfo, scopeid) for IPv6
    address: RawIPPort


class UDPSocket(UnreliableMessageStream[UDPPacket], _SocketWrapper[RawIPPort]):
    """An unconnected UDP socket."""


class ConnectedUDPSocket(UnreliableMessageStream[bytes], _ConnectedSocketWrapper[RawIPPort]):
    """
    A connected UDP socket.

    This socket sends packets to a predetermined address and port, and only receives packets from
    the same address/port combination.
    """


class TCPListener(Listener[TCPSocketStream], _SocketWrapper[RawIPPort]):
    """A TCP server socket."""


class UNIXListener(Listener[UNIXSocketStream], _SocketWrapper[str]):
    """A UNIX server socket."""
