import socket
import sys
from abc import ABCMeta, abstractmethod

import pytest

from anyio import (
    create_task_group, connect_tcp, create_udp_socket, connect_unix, create_unix_server,
    create_tcp_server, wait_all_tasks_blocked, sleep)
from anyio.abc.networking import UDPPacket
from anyio.abc.streams import Listener
from anyio.exceptions import (
    ClosedResourceError, BusyResourceError, ExceptionGroup)

# Check if the OS (and not just Python itself) supports IPv6
supports_ipv6 = False
if socket.has_ipv6:
    try:
        socket.socket(socket.AF_INET6)
        supports_ipv6 = True
    except OSError:
        pass

localhost_has_ipv6 = False
if supports_ipv6:
    localhost_has_ipv6 = any(addr[0] == socket.AddressFamily.AF_INET6
                             for addr in socket.getaddrinfo('localhost', 0))


@pytest.fixture
def fake_localhost_dns(monkeypatch):
    # Make it return IPv4 addresses first so we can test the IPv6 preference
    def fake_getaddrinfo(*args):
        return sorted(real_getaddrinfo(*args), key=lambda x: x[0])

    real_getaddrinfo = socket.getaddrinfo
    monkeypatch.setattr('socket.getaddrinfo', fake_getaddrinfo)


# class TestTCPStream:
#
#     @pytest.mark.parametrize('method_name, params', [
#         ('receive_until', [b'\n', 100]),
#         ('receive_exactly', [5])
#     ], ids=['read_until', 'read_exactly'])
#     @pytest.mark.anyio
#     async def test_read_partial(self, localhost, method_name, params):
#         async def server():
#             async with await stream_server.accept() as stream:
#                 method = getattr(stream, method_name)
#                 line1 = await method(*params)
#                 line2 = await method(*params)
#                 await stream.send_all(line1.strip() + line2.strip())
#
#         async with create_task_group() as tg:
#             async with await create_tcp_server(interface=localhost) as stream_server:
#                 await tg.spawn(server)
#                 async with await connect_tcp(localhost, stream_server.port) as client:
#                     await client.send_all(b'bla')
#                     await client.send_all(b'h\nb')
#                     await client.send_all(b'leh\n')
#                     response = await client.receive_some(100)
#
#         assert response == b'blahbleh'
#
#     @pytest.mark.anyio
#     async def test_send_large_buffer(self, localhost):
#         async def server():
#             async with await stream_server.accept() as stream:
#                 await stream.send_all(buffer)
#
#         buffer = b'\xff' * 1024 * 1024  # should exceed the maximum kernel send buffer size
#         async with create_task_group() as tg:
#             async with await create_tcp_server(interface=localhost) as stream_server:
#                 await tg.spawn(server)
#                 async with await connect_tcp(localhost, stream_server.port) as client:
#                     response = await client.read_exactly(len(buffer))
#                     with pytest.raises(IncompleteRead):
#                         await client.read_exactly(1)
#
#         assert response == buffer
#
#     @pytest.mark.parametrize('method_name, params', [
#         ('receive_until', [b'\n', 100]),
#         ('receive_exactly', [5])
#     ], ids=['read_until', 'read_exactly'])
#     @pytest.mark.anyio
#     async def test_incomplete_read(self, localhost, method_name, params):
#         async def server():
#             async with await stream_server.accept() as stream:
#                 await stream.send_all(b'bla')
#
#         async with create_task_group() as tg:
#             async with await create_tcp_server(interface=localhost) as stream_server:
#                 await tg.spawn(server)
#                 async with await connect_tcp(localhost, stream_server.port) as client:
#                     method = getattr(client, method_name)
#                     with pytest.raises(IncompleteRead):
#                         await method(*params)
#
#     @pytest.mark.anyio
#     async def test_delimiter_not_found(self, localhost):
#         async def server():
#             async with await stream_server.accept() as stream:
#                 await stream.send_all(b'blah\n')
#
#         async with create_task_group() as tg:
#             async with await create_tcp_server(interface=localhost) as stream_server:
#                 await tg.spawn(server)
#                 async with await connect_tcp(localhost, stream_server.port) as client:
#                     with pytest.raises(DelimiterNotFound) as exc:
#                         await client.read_until(b'\n', 3)
#
#                     assert exc.match(' first 3 bytes$')
#
#     @pytest.mark.anyio
#     async def test_receive_chunks(self, localhost):
#         async def server():
#             async with await stream_server.accept() as stream:
#                 async for chunk in stream.read_chunks(2):
#                     chunks.append(chunk)
#
#         chunks = []
#         async with await create_tcp_server(interface=localhost) as stream_server:
#             async with create_task_group() as tg:
#                 await tg.spawn(server)
#                 async with await connect_tcp(localhost, stream_server.port) as client:
#                     await client.send_all(b'blah')
#
#         assert chunks == [b'bl', b'ah']
#
#     @pytest.mark.anyio
#     async def test_buffer(self, localhost):
#         async def server():
#             async with await stream_server.accept() as stream:
#                 chunks.append(await stream.read_until(b'\n', 10))
#                 chunks.append(await stream.read_exactly(4))
#                 chunks.append(await stream.read_exactly(2))
#
#         chunks = []
#         async with await create_tcp_server(interface=localhost) as stream_server:
#             async with create_task_group() as tg:
#                 await tg.spawn(server)
#                 async with await connect_tcp(localhost, stream_server.port) as client:
#                     await client.send_all(b'blah\nfoobar')
#
#         assert chunks == [b'blah', b'foob', b'ar']
#
#     @pytest.mark.anyio
#     async def test_receive_delimited_chunks(self, localhost):
#         async def server():
#             async with await stream_server.accept() as stream:
#                 async for chunk in stream.read_delimited_chunks(b'\r\n', 8):
#                     chunks.append(chunk)
#
#         chunks = []
#         async with await create_tcp_server(interface=localhost) as stream_server:
#             async with create_task_group() as tg:
#                 await tg.spawn(server)
#                 async with await connect_tcp(localhost, stream_server.port) as client:
#                     for chunk in (b'bl', b'ah', b'\r', b'\nfoo', b'bar\r\n'):
#                         await client.send_all(chunk)
#
#         assert chunks == [b'blah', b'foobar']
#
#     @pytest.mark.parametrize('interface, target', [
#         (None, '127.0.0.1'),
#         (None, '::1'),
#         ('127.0.0.1', '127.0.0.1'),
#         ('::1', '::1'),
#         ('localhost', 'localhost'),
#     ], ids=['any_ipv4', 'any_ipv6', 'only_ipv4', 'only_ipv6', 'localhost'])
#     @pytest.mark.anyio
#     async def test_accept_connections(self, interface, target):
#         async def handle_client(stream):
#             async with stream:
#                 line = await stream.read_until(b'\n', 10)
#                 lines.add(line)
#
#             if len(lines) == 2:
#                 await stream_server.close()
#
#         async def server():
#             async for stream in stream_server.accept_connections():
#                 await tg.spawn(handle_client, stream)
#
#         lines = set()
#         with warnings.catch_warnings():
#             warnings.filterwarnings('ignore', category=DeprecationWarning,
#                                     module='anyio._networking')
#             stream_server = await create_tcp_server(interface=interface)
#
#         async with stream_server:
#             async with create_task_group() as tg:
#                 await tg.spawn(server)
#
#                 async with await connect_tcp(target, stream_server.port) as client:
#                     await client.send_all(b'client1\n')
#
#                 async with await connect_tcp(target, stream_server.port) as client:
#                     await client.send_all(b'client2\n')
#
#         assert lines == {b'client1', b'client2'}
##
#     @pytest.mark.xfail(condition=platform.system() == 'Darwin',
#                        reason='Occasionally fails on macOS')
#     @pytest.mark.anyio
#     async def test_concurrent_write(self, localhost):
#         async def send_data():
#             while True:
#                 await client.send_all(b'\x00' * 1024000)
#
#         async with await create_tcp_server(interface=localhost) as stream_server:
#             async with await connect_tcp(localhost, stream_server.port) as client:
#                 async with create_task_group() as tg:
#                     await tg.spawn(send_data)
#                     await wait_all_tasks_blocked()
#                     try:
#                         with pytest.raises(BusyResourceError) as exc:
#                             await client.send_all(b'foo')
#
#                         exc.match('already writing to')
#                     finally:
#                         await tg.cancel_scope.cancel()
#
#     @pytest.mark.anyio
#     async def test_concurrent_read(self, localhost):
#         async def receive_data():
#             await client.read_exactly(1)
#
#         async with await create_tcp_server(interface=localhost) as stream_server:
#             async with await connect_tcp(localhost, stream_server.port) as client:
#                 async with create_task_group() as tg:
#                     await tg.spawn(receive_data)
#                     await wait_all_tasks_blocked()
#                     try:
#                         with pytest.raises(BusyResourceError) as exc:
#                             await client.read_exactly(1)
#
#                         exc.match('already reading from')
#                     finally:
#                         await tg.cancel_scope.cancel()
#
#     @pytest.mark.skipif(not supports_ipv6, reason='IPv6 is not available')
#     @pytest.mark.parametrize('interface, expected_addr', [
#         (None, b'::1'),
#         ('127.0.0.1', b'127.0.0.1'),
#         ('::1', b'::1')
#     ])
#     @pytest.mark.anyio
#     async def test_happy_eyeballs(self, interface, expected_addr, fake_localhost_dns):
#         async def handle_client(stream):
#             addr, port, *rest = stream._socket._raw_socket.getpeername()
#             await stream.send_all(addr.encode() + b'\n')
#
#         async def server():
#             async for stream in stream_server.accept_connections():
#                 await tg.spawn(handle_client, stream)
#
#         async with await create_tcp_server(interface=interface) as stream_server:
#             async with create_task_group() as tg:
#                 await tg.spawn(server)
#                 async with await connect_tcp('localhost', stream_server.port) as client:
#                     assert await client.read_until(b'\n', 100) == expected_addr
#
#                 await stream_server.close()
#


class BaseSocketStreamTest(metaclass=ABCMeta):
    @classmethod
    async def serve(cls, listener: Listener):
        async with await listener.accept() as stream:  # noqa: F841
            await sleep(10)

    @pytest.fixture(params=[False, True], ids=['string', 'path'])
    def local_address(self, request, tmp_path_factory):
        as_path = request.param
        path = tmp_path_factory.mktemp('unix').joinpath('socket')
        return path if as_path else str(path)

    @classmethod
    @abstractmethod
    async def create_server(cls, local_address):
        pass

    @classmethod
    @abstractmethod
    async def connect_client(cls, remote_address):
        pass

    @pytest.mark.anyio
    async def test_send_receive(self, local_address):
        """Ensure that two-way communication between a server and a client works as expected."""
        async def server():
            async with await listener.accept() as stream:
                command = await stream.receive(100)
                await stream.send(command[::-1])

        async with await self.create_server(local_address) as listener:
            async with await self.connect_client(local_address) as client:
                async with create_task_group() as tg:
                    await tg.spawn(server)
                    await client.send(b'blah')
                    response = await client.receive(100)
                    assert response == b'halb'

    @pytest.mark.anyio
    async def test_accept_from_two_tasks(self, local_address):
        """
        Ensure that calling accept() on a listener which is already waiting to receive data results
        in BusyResourceError being raised.

        """
        async with await self.create_server(local_address) as listener:
            async with create_task_group() as tg:
                await tg.spawn(listener.accept)
                await wait_all_tasks_blocked()
                with pytest.raises(BusyResourceError):
                    await listener.accept()

                await tg.cancel_scope.cancel()

    @pytest.mark.anyio
    async def test_accept_from_closed_listener(self, local_address):
        """
        Ensure that calling accept() on a listener which has already been closed results in
        ClosedResourceError being raised.

        """
        listener = await self.create_server(local_address)
        await listener.aclose()
        with pytest.raises(ClosedResourceError):
            await listener.accept()

    @pytest.mark.anyio
    async def test_close_listener_during_accept(self, local_address):
        """
        Ensure that closing the listener while it is waiting to accept a new connection results in
        ClosedResourceError being raised.

        """
        listener = await self.create_server(local_address)
        with pytest.raises(ClosedResourceError):
            async with create_task_group() as tg:
                await tg.spawn(listener.accept)
                await wait_all_tasks_blocked()
                await listener.aclose()

    @pytest.mark.anyio
    async def test_close_stream_during_receive(self, local_address):
        """
        Ensure that closing the stream while it is waiting to receive data from the peer results in
        ClosedResourceError being raised.

        """
        async with await self.create_server(local_address) as listener:
            with pytest.raises(ClosedResourceError):
                async with create_task_group() as tg:
                    await tg.spawn(self.serve, listener)
                    client = await self.connect_client(local_address)
                    await tg.spawn(client.receive)
                    await wait_all_tasks_blocked()
                    await client.aclose()

            # Ensure that it also raises if trying to receive from an already closed stream
            with pytest.raises(ClosedResourceError):
                await client.receive()

    @pytest.mark.anyio
    async def test_close_stream_during_send(self, local_address):
        """
        Ensure that closing the stream while it is waiting to send data to the peer results in
        ClosedResourceError being raised.

        """
        async def send_loop():
            while True:
                await client.send(b'\x00' * 1024_000)

        async with await self.create_server(local_address) as listener:
            with pytest.raises(ClosedResourceError):
                async with create_task_group() as tg:
                    await tg.spawn(self.serve, listener)
                    client = await self.connect_client(local_address)
                    await tg.spawn(send_loop)
                    await wait_all_tasks_blocked()
                    await client.aclose()

            # Ensure that it also raises if trying to receive from an already closed stream
            with pytest.raises(ClosedResourceError):
                await client.send(b'\x00')

    @pytest.mark.anyio
    async def test_cancel_receive(self, local_address):
        """
        Ensure that a task with a pending cancellation will not actually receive from the stream.

        """
        async def serve():
            async with await listener.accept() as stream:
                await stream.send(b'data')
                async for _ in stream:
                    pass

        async with await self.create_server(local_address) as listener:
            async with create_task_group() as tg:
                await tg.spawn(serve)
                async with await self.connect_client(local_address) as client:
                    async with create_task_group() as subgroup:
                        await subgroup.spawn(client.receive)
                        await subgroup.cancel_scope.cancel()

                    await wait_all_tasks_blocked()
                    payload = await client.receive()
                    assert payload == b'data'

    @pytest.mark.anyio
    async def test_cancel_send(self, local_address):
        """
        Ensure that a task with a pending cancellation will not actually send from the stream.

        """
        async def serve():
            async with await listener.accept() as stream:
                await stream.send(b'data')
                async for _ in stream:
                    pass

        async with await self.create_server(local_address) as listener:
            async with create_task_group() as tg:
                await tg.spawn(serve)
                async with await self.connect_client(local_address) as client:
                    async with create_task_group() as subgroup:
                        await subgroup.spawn(client.receive)
                        await subgroup.cancel_scope.cancel()

                    await wait_all_tasks_blocked()
                    payload = await client.receive()
                    assert payload == b'data'

    @pytest.mark.anyio
    async def test_close_listener_iterate_accept(self, local_address):
        """
        Ensure that iterating over the listener will accept new connections until the listener is
        closed, and that this will cleanly end the iteration.

        """
        async def serve():
            nonlocal num_connections
            async for conn in listener:
                num_connections += 1
                await conn.send(b'data')

        num_connections = 0
        async with await self.create_server(local_address) as listener:
            async with create_task_group() as tg:
                await tg.spawn(serve)
                for _ in range(3):
                    client = await self.connect_client(local_address)
                    assert await client.receive(4) == b'data'
                    await client.aclose()

                await listener.aclose()

        assert num_connections == 3

    @pytest.mark.anyio
    async def test_iterate_stream(self, local_address):
        """Ensure that iterating over the stream will yield the received bytes."""
        async def serve():
            async with await listener.accept() as conn:
                await conn.send(b'this ')
                await conn.send(b'is a ')
                await conn.send(b'packet')

        async with await self.create_server(local_address) as listener:
            async with create_task_group() as tg:
                await tg.spawn(serve)
                buffer = b''
                client = await self.connect_client(local_address)
                async for data in client:
                    buffer += data

        assert buffer == b'this is a packet'

    @pytest.mark.anyio
    async def test_socket_options(self, local_address):
        async with await self.create_server(local_address) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 80000)
            assert listener.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF) in (80000, 160000)
            async with await self.connect_client(local_address) as client:
                client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 80000)
                assert client.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF) in (80000, 160000)


class TestTCPSocketStream(BaseSocketStreamTest):
    @pytest.fixture(params=[
        pytest.param(socket.AF_INET, id='ipv4'),
        pytest.param(socket.AF_INET6, id='ipv6',
                     marks=[pytest.mark.skipif(not supports_ipv6, reason='no IPv6 support')])
    ])
    def local_address(self, request):
        addr = '127.0.0.1' if request.param == socket.AF_INET else '::1'
        sock = socket.socket(request.param)
        sock.bind((addr, 0))
        port = sock.getsockname()[1]
        sock.close()
        return addr, port

    @classmethod
    async def create_server(cls, local_address):
        addr, port = local_address
        return await create_tcp_server(port, addr)

    @classmethod
    async def connect_client(cls, remote_address):
        return await connect_tcp(*remote_address)

    @pytest.mark.anyio
    async def test_nodelay(self, local_address):
        """
        Ensure that both client connections and accepted server connections have the TCP_NODELAY
        flag set.

        """
        async with await self.create_server(local_address) as listener:
            client = await self.connect_client(local_address)
            async with await listener.accept() as connection:
                assert client.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) != 0
                assert connection.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) != 0

    @pytest.mark.anyio
    async def test_socket_creation_failure(self, monkeypatch):
        def fake_create_socket(*args):
            raise OSError('Bogus error')

        monkeypatch.setattr(socket, 'socket', fake_create_socket)
        with pytest.raises(OSError) as exc:
            await connect_tcp('127.0.0.1', 1111)

        exc.match('All connection attempts failed')
        assert isinstance(exc.value.__cause__, OSError)
        assert str(exc.value.__cause__) == 'Bogus error'

    @pytest.mark.parametrize('target, exception_class', [
        pytest.param(
            'localhost', ExceptionGroup, id='multi',
            marks=[pytest.mark.skipif(not supports_ipv6, reason='IPv6 is not available'),
                   pytest.mark.skipif(not localhost_has_ipv6,
                                      reason='localhost does not resolve to an IPv6 address')]
        ),
        pytest.param('127.0.0.1', ConnectionRefusedError, id='single')
    ])
    @pytest.mark.anyio
    async def test_connrefused(self, target, exception_class, fake_localhost_dns):
        dummy_socket = socket.socket(socket.AF_INET6)
        dummy_socket.bind(('::', 0))
        free_port = dummy_socket.getsockname()[1]
        dummy_socket.close()

        with pytest.raises(OSError) as exc:
            await connect_tcp(target, free_port)

        assert exc.match('All connection attempts failed')
        assert isinstance(exc.value.__cause__, exception_class)
        if exception_class is ExceptionGroup:
            for exc in exc.value.__cause__.exceptions:
                assert isinstance(exc, ConnectionRefusedError)


@pytest.mark.skipif(sys.platform == 'win32', reason='UNIX sockets are not available on Windows')
class TestUNIXSocketStream(BaseSocketStreamTest):
    @pytest.fixture(params=[False, True], ids=['string', 'path'])
    def local_address(self, request, tmp_path_factory):
        as_path = request.param
        path = tmp_path_factory.mktemp('unix').joinpath('socket')
        yield path if as_path else str(path)
        assert path.is_socket()

    @classmethod
    async def create_server(cls, local_address):
        return await create_unix_server(local_address)

    @classmethod
    async def connect_client(cls, remote_address):
        return await connect_unix(remote_address)


class TestUDPSocket:
    @pytest.fixture(params=[
        pytest.param('127.0.0.1', id='ipv4'),
        pytest.param('::1', id='ipv6')
    ])
    def localhost(self, request, anyio_backend):
        if supports_ipv6:
            if sys.platform == 'win32' and anyio_backend == 'asyncio':
                if sys.version_info >= (3, 8):
                    pytest.skip('Would hang due to https://bugs.python.org/issue39148')

        return request.param

    @pytest.mark.anyio
    async def test_unconnected_receive_packets(self, localhost):
        async def serve():
            async for packet in server:
                await server.send(UDPPacket(packet.data[::-1], packet.address))

        async with await create_udp_socket(interface=localhost) as server:
            async with await create_udp_socket(interface=localhost) as client:
                async with create_task_group() as tg:
                    await tg.spawn(serve)
                    await client.send(UDPPacket(b'FOOBAR', server.local_address))
                    assert await client.receive() == (b'RABOOF', server.local_address)
                    await client.send(UDPPacket(b'123456', server.local_address))
                    assert await client.receive() == (b'654321', server.local_address)
                    await server.aclose()

    @pytest.mark.anyio
    async def test_connected_receive_packets(self, localhost):
        async def serve():
            async for packet in server:
                await server.send(UDPPacket(packet.data[::-1], packet.address))

        async with await create_udp_socket(interface=localhost) as server:
            host, port = server.local_address[:2]
            async with await create_udp_socket(target_host=host, target_port=port) as client:
                async with create_task_group() as tg:
                    await tg.spawn(serve)
                    await client.send(b'FOOBAR')
                    assert await client.receive() == b'RABOOF'
                    await client.send(b'123456')
                    assert await client.receive() == b'654321'
                    await server.aclose()

    @pytest.mark.parametrize('connect', [False, True], ids=['unconnected', 'connected'])
    @pytest.mark.anyio
    async def test_receive_send_from_closed_stream(self, localhost, connect):
        """
        Ensure that trying to receive or send from an already closed socket results in
        ClosedResourceError being raised.

        """
        target_host, target_port = (localhost, 9999) if connect else (None, None)
        payload = b'data' if connect else UDPPacket(b'data', (localhost, 9999))
        receiver = await create_udp_socket(interface=localhost, target_host=target_host,
                                           target_port=target_port)
        await receiver.aclose()
        with pytest.raises(ClosedResourceError):
            await receiver.receive()
        with pytest.raises(ClosedResourceError):
            await receiver.send(payload)

    @pytest.mark.parametrize('connect', [False, True], ids=['unconnected', 'connected'])
    @pytest.mark.anyio
    async def test_handle_close_while_receiving(self, localhost, connect):
        """
        Ensure that if the socket is closed while waiting to receive a packet, the receive()
        operation raises a ClosedResourceError.

        """
        async def receive():
            with pytest.raises(ClosedResourceError):
                await receiver.receive()

        async with create_task_group() as tg:
            target_host, target_port = (localhost, 9999) if connect else (None, None)
            async with await create_udp_socket(interface=localhost, target_host=target_host,
                                               target_port=target_port) as receiver:
                await tg.spawn(receive)
                await wait_all_tasks_blocked()
                await receiver.aclose()

    @pytest.mark.parametrize('connect', [False, True], ids=['unconnected', 'connected'])
    @pytest.mark.anyio
    async def test_receive_from_two_tasks(self, localhost, connect):
        """
        Ensure that calling receive() on a socket which is already waiting to receive data results
        in BusyResourceError being raised.

        """
        target_host, target_port = (localhost, 9999) if connect else (None, None)
        async with await create_udp_socket(interface=localhost, target_host=target_host,
                                           target_port=target_port) as receiver:
            async with create_task_group() as tg:
                await tg.spawn(receiver.receive)
                await wait_all_tasks_blocked()
                with pytest.raises(BusyResourceError):
                    await receiver.receive()

                await tg.cancel_scope.cancel()

    @pytest.mark.parametrize('connect', [False, True], ids=['unconnected', 'connected'])
    @pytest.mark.anyio
    async def test_cancel_receive(self, localhost, connect):
        """
        Ensure that a task with a pending cancellation will not actually receive from the socket.

        """
        async with await create_udp_socket(interface=localhost) as sender:
            target_host, target_port = sender.local_address[:2] if connect else (None, None)
            async with await create_udp_socket(interface=localhost, target_host=target_host,
                                               target_port=target_port) as receiver:
                async with create_task_group() as tg:
                    await sender.send(UDPPacket(b'data', receiver.local_address))
                    await tg.spawn(receiver.receive)
                    await tg.cancel_scope.cancel()

                result = await receiver.receive()
                payload = result if connect else result.data
                assert payload == b'data'

    @pytest.mark.parametrize('connect', [False, True], ids=['unconnected', 'connected'])
    @pytest.mark.anyio
    async def test_cancel_send(self, localhost, connect):
        """
        Ensure that a task with a pending cancellation will not actually send from the socket.

        """
        async with await create_udp_socket(interface=localhost) as receiver:
            target_host, target_port = receiver.local_address[:2] if connect else (None, None)
            lost_payload = b'lost' if connect else UDPPacket(b'lost', receiver.local_address)
            real_payload = b'data' if connect else UDPPacket(b'data', receiver.local_address)
            async with await create_udp_socket(interface=localhost, target_host=target_host,
                                               target_port=target_port) as sender:
                async with create_task_group() as tg:
                    await tg.spawn(sender.send, lost_payload)
                    await tg.cancel_scope.cancel()

                await sender.send(real_payload)
                packet = await receiver.receive()
                assert packet.data == b'data'

    @pytest.mark.anyio
    async def test_raw_socket(self, localhost):
        """Ensure that the value of the raw_socket property matches our expectations."""
        async with await create_udp_socket(interface=localhost) as udp:
            assert isinstance(udp.raw_socket, socket.SocketType)
            assert udp.raw_socket.family in (socket.AF_INET, socket.AF_INET6)
