import socket
import ssl
from contextlib import ExitStack
from pathlib import Path
from threading import Thread

import pytest

from anyio import connect_tcp
from anyio.wrappers import TLSWrapperStream


class TestBufferedStream:
    async def test_read_exactly(self):
        pass

    async def test_read_until(self):
        pass

    async def test_read_chunks(self):
        pass


class TestTextWrapper:
    pass


class TestTLSWrapper:
    @pytest.fixture
    def server_context(self):
        server_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        server_context.load_cert_chain(certfile=str(Path(__file__).with_name('cert.pem')),
                                       keyfile=str(Path(__file__).with_name('key.pem')))
        return server_context

    @pytest.fixture
    def client_context(self):
        client_context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        client_context.load_verify_locations(cafile=str(Path(__file__).with_name('cert.pem')))
        return client_context

    @pytest.mark.anyio
    async def test_send_receive(self, server_context, client_context):
        def serve_sync():
            conn, addr = server_sock.accept()
            conn.settimeout(1)
            data = conn.recv(10)
            conn.send(data[::-1])
            conn.close()

        server_sock = server_context.wrap_socket(socket.socket(), server_side=True,
                                                 suppress_ragged_eofs=False)
        server_sock.settimeout(1)
        server_sock.bind(('127.0.0.1', 0))
        server_sock.listen()
        server_thread = Thread(target=serve_sync)
        server_thread.start()

        async with await connect_tcp(*server_sock.getsockname()) as stream:
            wrapper = await TLSWrapperStream.wrap(stream, 'localhost', context=client_context)
            await wrapper.send(b'hello')
            response = await wrapper.receive()

        server_thread.join()
        assert response == b'olleh'

    @pytest.mark.anyio
    async def test_unwrap(self, server_context, client_context):
        def serve_sync():
            conn, addr = server_sock.accept()
            conn.settimeout(1)
            conn.send(b'encrypted')
            unencrypted = conn.unwrap()
            unencrypted.send(b'unencrypted')
            unencrypted.close()

        server_context.set_alpn_protocols(['dummy1', 'dummy2'])
        client_context.set_alpn_protocols(['dummy2', 'dummy3'])

        server_sock = server_context.wrap_socket(socket.socket(), server_side=True,
                                                 suppress_ragged_eofs=False)
        server_sock.settimeout(1)
        server_sock.bind(('127.0.0.1', 0))
        server_sock.listen()
        server_thread = Thread(target=serve_sync)
        server_thread.start()

        async with await connect_tcp(*server_sock.getsockname()) as stream:
            wrapper = await TLSWrapperStream.wrap(stream, 'localhost', context=client_context)
            msg1 = await wrapper.receive()
            stream, msg2 = await wrapper.unwrap()
            msg2 += await stream.receive()

        server_thread.join()
        assert msg1 == b'encrypted'
        assert msg2 == b'unencrypted'

    @pytest.mark.skipif(not ssl.HAS_ALPN, reason='ALPN support not available')
    @pytest.mark.anyio
    async def test_alpn_negotiation(self, server_context, client_context):
        def serve_sync():
            conn, addr = server_sock.accept()
            conn.settimeout(1)
            conn.send(conn.selected_alpn_protocol().encode())
            conn.close()

        server_context.set_alpn_protocols(['dummy1', 'dummy2'])
        client_context.set_alpn_protocols(['dummy2', 'dummy3'])

        server_sock = server_context.wrap_socket(socket.socket(), server_side=True,
                                                 suppress_ragged_eofs=False)
        server_sock.settimeout(1)
        server_sock.bind(('127.0.0.1', 0))
        server_sock.listen()
        server_thread = Thread(target=serve_sync)
        server_thread.start()

        async with await connect_tcp(*server_sock.getsockname()) as stream:
            wrapper = await TLSWrapperStream.wrap(stream, 'localhost', context=client_context)
            server_alpn_protocol = await wrapper.receive()

        server_thread.join()
        assert wrapper.alpn_protocol == 'dummy2'
        assert server_alpn_protocol == b'dummy2'

    @pytest.mark.parametrize('server_compatible, client_compatible', [
        pytest.param(True, True, id='both_standard'),
        pytest.param(True, False, id='server_standard'),
        pytest.param(False, True, id='client_standard'),
        pytest.param(False, False, id='neither_standard')
    ])
    @pytest.mark.anyio
    async def test_ragged_eofs(self, server_context, client_context, server_compatible,
                               client_compatible):
        def serve_sync():
            conn, addr = server_sock.accept()
            conn.settimeout(1)
            with server_cm:
                if server_compatible:
                    conn.unwrap()

            conn.close()

        client_cm = ExitStack()
        server_cm = ExitStack()
        if server_compatible and not client_compatible:
            server_cm = pytest.raises((ssl.SSLEOFError, ssl.SSLSyscallError))
        elif client_compatible and not server_compatible:
            client_cm = pytest.raises((ssl.SSLEOFError, ssl.SSLSyscallError))

        server_sock = server_context.wrap_socket(socket.socket(), server_side=True,
                                                 suppress_ragged_eofs=not server_compatible)
        server_sock.settimeout(1)
        server_sock.bind(('127.0.0.1', 0))
        server_sock.listen()
        server_thread = Thread(target=serve_sync)
        server_thread.start()

        stream = await connect_tcp(*server_sock.getsockname())
        wrapper = await TLSWrapperStream.wrap(stream, 'localhost', context=client_context,
                                              tls_standard_compatible=client_compatible)
        with client_cm:
            await wrapper.aclose()

        server_thread.join()

    # @pytest.mark.anyio
    # async def test_handshake_on_connect(self, server_context, client_context):
    #     async def server():
    #         nonlocal server_binding
    #         async with await stream_server.accept() as stream:
    #             assert stream.server_side
    #             assert stream.server_hostname is None
    #             assert stream.tls_version.startswith('TLSv')
    #             assert stream.cipher in stream.shared_ciphers
    #             server_binding = stream.get_channel_binding()
    #
    #             command = await stream.receive_some(100)
    #             await stream.send_all(command[::-1])
    #
    #     server_binding = None
    #     async with create_task_group() as tg:
    #         async with await create_tcp_server(ssl_context=server_context) as stream_server:
    #             await tg.spawn(server)
    #             async with await connect_tcp(
    #                     'localhost', stream_server.port, ssl_context=client_context,
    #                     autostart_tls=True) as client:
    #                 assert not client.server_side
    #                 assert client.server_hostname == 'localhost'
    #                 assert client.tls_version.startswith('TLSv')
    #                 assert client.cipher in client.shared_ciphers
    #                 client_binding = client.get_channel_binding()
    #
    #                 await client.send_all(b'blah')
    #                 response = await client.receive_some(100)
    #
    #     assert response == b'halb'
    #     assert client_binding == server_binding
    #     assert isinstance(client_binding, bytes)
    #
    #
    # @pytest.mark.anyio
    # async def test_manual_handshake(self, server_context, client_context):
    #     async def server():
    #         async with await stream_server.accept() as stream:
    #             assert stream.tls_version is None
    #
    #             while True:
    #                 command = await stream.read_exactly(5)
    #                 if command == b'START':
    #                     await stream.start_tls()
    #                     assert stream.tls_version.startswith('TLSv')
    #                 elif command == b'CLOSE':
    #                     break
    #
    #     async with await create_tcp_server(ssl_context=server_context,
    #                                        autostart_tls=False) as stream_server:
    #         async with create_task_group() as tg:
    #             await tg.spawn(server)
    #             async with await connect_tcp('localhost', stream_server.port,
    #                                          ssl_context=client_context) as client:
    #                 assert client.tls_version is None
    #
    #                 await client.send_all(b'START')  # arbitrary string
    #                 await client.start_tls()
    #
    #                 assert client.tls_version.startswith('TLSv')
    #                 await client.send_all(b'CLOSE')  # arbitrary string
    #
    # @pytest.mark.anyio
    # async def test_buffer(self, server_context, client_context):
    #     async def server():
    #         async with await stream_server.accept() as stream:
    #             chunks.append(await stream.read_until(b'\n', 10))
    #             chunks.append(await stream.read_exactly(4))
    #             chunks.append(await stream.read_exactly(2))
    #
    #     chunks = []
    #     async with await create_tcp_server(ssl_context=server_context) as stream_server:
    #         async with create_task_group() as tg:
    #             await tg.spawn(server)
    #             async with await connect_tcp(
    #                     'localhost', stream_server.port, ssl_context=client_context,
    #                     autostart_tls=True) as client:
    #                 await client.send_all(b'blah\nfoobar')
    #
    #     assert chunks == [b'blah', b'foob', b'ar']
    #
