import math
import socket
import sys
from collections import OrderedDict
from concurrent.futures import Future
from functools import partial
from threading import Thread
from typing import (
    Callable, Set, Optional, Coroutine, Any, cast, Dict, List, Sequence, ClassVar,
    Generic, Type)
from weakref import WeakKeyDictionary

import attr
import curio.io
import curio.meta
import curio.socket
import curio.ssl
import curio.subprocess
import curio.traps

from .. import T_Retval, claim_worker_thread, TaskInfo, _local, T_Item
from ..abc.networking import (
    TCPSocketStream as AbstractTCPSocketStream, UNIXSocketStream as AbstractUNIXSocketStream,
    UDPPacket, UDPSocket as AbstractUDPSocket, ConnectedUDPSocket as AbstractConnectedUDPSocket,
    TCPListener as AbstractTCPListener, UNIXListener as AbstractUNIXListener
)
from ..abc.streams import ByteStream, MessageStream, ReceiveByteStream, SendByteStream
from ..abc.subprocesses import AsyncProcess as AbstractAsyncProcess
from ..abc.synchronization import (
    Lock as AbstractLock, Condition as AbstractCondition, Event as AbstractEvent,
    Semaphore as AbstractSemaphore, CapacityLimiter as AbstractCapacityLimiter)
from ..abc.tasks import CancelScope as AbstractCancelScope, TaskGroup as AbstractTaskGroup
from ..exceptions import (
    ExceptionGroup as BaseExceptionGroup, ClosedResourceError, BusyResourceError, WouldBlock)

if sys.version_info < (3, 7):
    from async_generator import asynccontextmanager
else:
    from contextlib import asynccontextmanager


#
# Event loop
#

def run(func: Callable[..., T_Retval], *args, **curio_options) -> T_Retval:
    async def wrapper():
        nonlocal exception, retval
        try:
            retval = await func(*args)
        except BaseException as exc:
            exception = exc

    exception = retval = None
    curio.run(wrapper, **curio_options)
    if exception is not None:
        raise exception
    else:
        return cast(T_Retval, retval)


#
# Miscellaneous functions
#

finalize = curio.meta.finalize


async def sleep(delay: float):
    await check_cancelled()
    await curio.sleep(delay)


#
# Timeouts and cancellation
#

CancelledError = curio.TaskCancelled


class CancelScope:
    __slots__ = ('_deadline', '_shield', '_parent_scope', '_cancel_called', '_active',
                 '_timeout_task', '_tasks', '_host_task', '_timeout_expired')

    def __init__(self, deadline: float = math.inf, shield: bool = False):
        self._deadline = deadline
        self._shield = shield
        self._parent_scope = None
        self._cancel_called = False
        self._active = False
        self._timeout_task = None
        self._tasks = set()  # type: Set[curio.Task]
        self._host_task = None  # type: Optional[curio.Task]
        self._timeout_expired = False

    async def __aenter__(self):
        async def timeout():
            await curio.sleep(self._deadline - await curio.clock())
            self._timeout_expired = True
            await self.cancel()

        if self._active:
            raise RuntimeError(
                "Each CancelScope may only be used for a single 'async with' block"
            )

        self._host_task = await curio.current_task()
        self._tasks.add(self._host_task)
        try:
            task_state = _task_states[self._host_task]
        except KeyError:
            task_state = TaskState(self)
            _task_states[self._host_task] = task_state
        else:
            self._parent_scope = task_state.cancel_scope
            task_state.cancel_scope = self

        if self._deadline != math.inf:
            self._timeout_task = await curio.spawn(timeout)
            if await curio.clock() >= self._deadline:
                self._cancel_called = True

        self._active = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._active = False
        if self._timeout_task:
            await self._timeout_task.cancel(blocking=False)

        self._tasks.remove(self._host_task)
        host_task_state = _task_states.get(self._host_task)
        if host_task_state is not None and host_task_state.cancel_scope is self:
            host_task_state.cancel_scope = self._parent_scope

        exceptions = exc_val.exceptions if isinstance(exc_val, ExceptionGroup) else [exc_val]
        if all(isinstance(exc, CancelledError) for exc in exceptions):
            if self._timeout_expired:
                return True
            elif not self._parent_cancelled():
                # This scope was directly cancelled
                return True

    async def _cancel(self):
        # Deliver cancellation to directly contained tasks and nested cancel scopes
        for task in self._tasks:
            # Cancel the task directly, but only if it's blocked and isn't within a shielded scope
            cancel_scope = _task_states[task].cancel_scope
            if cancel_scope is self:
                # Only deliver the cancellation if the task is already running (but not this task!)
                if not task.coro.cr_running and task.coro.cr_await is not None:
                    await task.cancel(blocking=False)
            elif not cancel_scope._shielded_to(self):
                await cancel_scope._cancel()

    def _shielded_to(self, parent: 'CancelScope') -> bool:
        # Check whether this task or any parent up to (but not including) the "parent" argument is
        # shielded
        cancel_scope = self  # type: Optional[CancelScope]
        while cancel_scope is not None and cancel_scope is not parent:
            if cancel_scope._shield:
                return True
            else:
                cancel_scope = cancel_scope._parent_scope

        return False

    def _parent_cancelled(self) -> bool:
        # Check whether any parent has been cancelled
        cancel_scope = self._parent_scope
        while cancel_scope is not None and not cancel_scope._shield:
            if cancel_scope._cancel_called:
                return True
            else:
                cancel_scope = cancel_scope._parent_scope

        return False

    async def cancel(self) -> None:
        if self._cancel_called:
            return

        self._cancel_called = True
        await self._cancel()

    @property
    def deadline(self) -> float:
        return self._deadline

    @property
    def cancel_called(self) -> bool:
        return self._cancel_called

    @property
    def shield(self) -> bool:
        return self._shield


AbstractCancelScope.register(CancelScope)


async def check_cancelled():
    try:
        cancel_scope = _task_states[await curio.current_task()].cancel_scope
    except KeyError:
        return

    while cancel_scope:
        if cancel_scope.cancel_called:
            raise CancelledError
        elif cancel_scope.shield:
            return
        else:
            cancel_scope = cancel_scope._parent_scope


@asynccontextmanager
@curio.meta.safe_generator
async def fail_after(delay: float, shield: bool):
    deadline = await curio.clock() + delay
    async with CancelScope(deadline, shield) as scope:
        yield scope

    if scope._timeout_expired:
        raise TimeoutError


@asynccontextmanager
@curio.meta.safe_generator
async def move_on_after(delay: float, shield: bool):
    deadline = await curio.clock() + delay
    async with CancelScope(deadline=deadline, shield=shield) as scope:
        yield scope


async def current_effective_deadline():
    deadline = math.inf
    cancel_scope = _task_states[await curio.current_task()].cancel_scope
    while cancel_scope:
        deadline = min(deadline, cancel_scope.deadline)
        if cancel_scope.shield:
            break
        else:
            cancel_scope = cancel_scope._parent_scope

    return deadline


async def current_time():
    return await curio.clock()


#
# Task states
#

class TaskState:
    """
    Encapsulates auxiliary task information that cannot be added to the Task instance itself
    because there are no upstream guarantees that no attribute conflicts will occur.
    """

    __slots__ = 'cancel_scope'

    def __init__(self, cancel_scope: Optional[CancelScope]):
        self.cancel_scope = cancel_scope


_task_states = WeakKeyDictionary()  # type: WeakKeyDictionary[curio.Task, TaskState]


#
# Task groups
#

class ExceptionGroup(BaseExceptionGroup):
    def __init__(self, exceptions: Sequence[BaseException]):
        super().__init__()
        self.exceptions = exceptions


class TaskGroup:
    __slots__ = 'cancel_scope', '_active', '_exceptions'

    def __init__(self) -> None:
        self.cancel_scope = CancelScope()
        self._active = False
        self._exceptions = []  # type: List[BaseException]

    async def __aenter__(self):
        await self.cancel_scope.__aenter__()
        self._active = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        ignore_exception = await self.cancel_scope.__aexit__(exc_type, exc_val, exc_tb)
        if exc_val is not None:
            await self.cancel_scope.cancel()
            if not ignore_exception:
                self._exceptions.append(exc_val)

        while self.cancel_scope._tasks:
            for task in self.cancel_scope._tasks.copy():
                await task.wait()

        self._active = False
        if not self.cancel_scope._parent_cancelled():
            exceptions = self._filter_cancellation_errors(self._exceptions)
        else:
            exceptions = self._exceptions

        if len(exceptions) > 1:
            raise ExceptionGroup(exceptions)
        elif exceptions and exceptions[0] is not exc_val:
            raise exceptions[0]

        return ignore_exception

    @staticmethod
    def _filter_cancellation_errors(exceptions: Sequence[BaseException]) -> List[BaseException]:
        filtered_exceptions = []  # type: List[BaseException]
        for exc in exceptions:
            if isinstance(exc, ExceptionGroup):
                exc.exceptions = TaskGroup._filter_cancellation_errors(exc.exceptions)
                if exc.exceptions:
                    if len(exc.exceptions) > 1:
                        filtered_exceptions.append(exc)
                    else:
                        filtered_exceptions.append(exc.exceptions[0])
            elif not isinstance(exc, CancelledError):
                filtered_exceptions.append(exc)

        return filtered_exceptions

    async def _run_wrapped_task(self, func: Callable[..., Coroutine], args: tuple) -> None:
        task = await curio.current_task()
        try:
            await func(*args)
        except BaseException as exc:
            self._exceptions.append(exc)
            await self.cancel_scope.cancel()
        finally:
            self.cancel_scope._tasks.remove(task)
            del _task_states[task]

    async def spawn(self, func: Callable[..., Coroutine], *args, name=None) -> None:
        if not self._active:
            raise RuntimeError('This task group is not active; no new tasks can be spawned.')

        task = await curio.spawn(self._run_wrapped_task, func, args, daemon=True,
                                 report_crash=False)
        task.parentid = (await curio.current_task()).id
        if name is not None:
            task.name = name

        # Make the spawned task inherit the task group's cancel scope
        _task_states[task] = TaskState(cancel_scope=self.cancel_scope)
        self.cancel_scope._tasks.add(task)


AbstractTaskGroup.register(TaskGroup)


#
# Threads
#

async def run_in_thread(func: Callable[..., T_Retval], *args, cancellable: bool = False,
                        limiter: Optional['CapacityLimiter'] = None) -> T_Retval:
    async def async_call_helper():
        while True:
            item = await queue.get()
            if item is None:
                await limiter.release_on_behalf_of(task)
                await finish_event.set()
                return

            func_, args_, f = item  # type: Callable, tuple, Future
            try:
                retval_ = await func_(*args_)
            except BaseException as exc_:
                f.set_exception(exc_)
            else:
                f.set_result(retval_)

    def thread_worker():
        nonlocal retval, exception
        try:
            with claim_worker_thread('curio'):
                _local.queue = queue
                retval = func(*args)
        except BaseException as exc:
            exception = exc
        finally:
            if not helper_task.cancelled:
                queue.put(None)

    await check_cancelled()
    task = await curio.current_task()
    queue = curio.UniversalQueue(maxsize=1)
    finish_event = curio.Event()
    retval, exception = None, None
    limiter = limiter or _default_thread_limiter
    await limiter.acquire_on_behalf_of(task)
    thread = Thread(target=thread_worker, daemon=True)
    helper_task = await curio.spawn(async_call_helper, daemon=True, report_crash=False)
    thread.start()
    async with CancelScope(shield=not cancellable):
        await finish_event.wait()

    if exception is not None:
        raise exception
    else:
        return cast(T_Retval, retval)


def run_async_from_thread(func: Callable[..., T_Retval], *args) -> T_Retval:
    future = Future()  # type: Future[T_Retval]
    _local.queue.put((func, args, future))
    return future.result()


#
# Stream wrappers
#

class FileStreamWrapper(ByteStream):
    def __init__(self, stream: curio.io.FileStream):
        super().__init__()
        self._stream = stream

    async def receive(self, max_bytes: Optional[int] = None) -> bytes:
        return await self._stream.read(max_bytes or 65536)

    async def send(self, item: bytes) -> None:
        await self._stream.write(item)

    async def aclose(self) -> None:
        await self._stream.close()


#
# Subprocesses
#

@attr.s(slots=True, auto_attribs=True)
class Process(AbstractAsyncProcess):
    _process: curio.subprocess.Popen
    _stdin: Optional[SendByteStream]
    _stdout: Optional[ReceiveByteStream]
    _stderr: Optional[ReceiveByteStream]

    async def wait(self) -> int:
        return await self._process.wait()

    def terminate(self) -> None:
        self._process.terminate()

    def kill(self) -> None:
        self._process.kill()

    def send_signal(self, signal: int) -> None:
        self._process.send_signal(signal)

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> Optional[int]:
        return self._process.returncode

    @property
    def stdin(self) -> Optional[SendByteStream]:
        return self._stdin

    @property
    def stdout(self) -> Optional[ReceiveByteStream]:
        return self._stdout

    @property
    def stderr(self) -> Optional[ReceiveByteStream]:
        return self._stderr


async def open_process(command, *, shell: bool, stdin: int, stdout: int, stderr: int):
    await check_cancelled()
    process = curio.subprocess.Popen(command, stdin=stdin, stdout=stdout, stderr=stderr,
                                     shell=shell)
    stdin_stream = FileStreamWrapper(process.stdin) if process.stdin else None
    stdout_stream = FileStreamWrapper(process.stdout) if process.stdout else None
    stderr_stream = FileStreamWrapper(process.stderr) if process.stderr else None
    return Process(process, stdin_stream, stdout_stream, stderr_stream)


#
# Async file I/O
#

async def aopen(*args, **kwargs):
    fp = await run_in_thread(partial(open, *args, **kwargs))
    return curio.file.AsyncFile(fp)


#
# Sockets and networking
#

@attr.s(slots=True, auto_attribs=True)
class StreamMixin:
    raw_socket: socket.SocketType
    _curio_socket: curio.io.Socket
    _receiver_task: Optional[curio.Task] = None
    _sender_task: Optional[curio.Task] = None

    async def aclose(self) -> None:
        self.raw_socket.close()
        if self._receiver_task:
            await self._receiver_task.cancel(exc=ClosedResourceError)
        if self._sender_task:
            await self._sender_task.cancel(exc=ClosedResourceError)

    async def receive(self, max_bytes: Optional[int] = None) -> bytes:
        await check_cancelled()
        if self.raw_socket.fileno() < 0:
            raise ClosedResourceError
        if self._receiver_task:
            raise BusyResourceError('receiving from')

        self._receiver_task = await curio.current_task()
        try:
            return await self._curio_socket.recv(max_bytes or 65536)
        except (ConnectionResetError, OSError) as exc:
            self.raw_socket.close()
            raise ClosedResourceError from exc
        finally:
            self._receiver_task = None

    async def send(self, item: bytes) -> None:
        await check_cancelled()
        if self.raw_socket.fileno() < 0:
            raise ClosedResourceError
        if self._sender_task:
            raise BusyResourceError('sending from')

        self._sender_task = await curio.current_task()
        try:
            await self._curio_socket.sendall(item)
        except (ConnectionResetError, OSError) as exc:
            self.raw_socket.close()
            raise ClosedResourceError from exc
        finally:
            self._sender_task = None


@attr.s(slots=True, auto_attribs=True)
class ListenerMixin:
    stream_class: ClassVar[Type[ByteStream]]
    raw_socket: socket.SocketType
    _curio_socket: curio.io.Socket
    _accepter_task: Optional[curio.Task] = None

    async def aclose(self):
        self.raw_socket.close()
        if self._accepter_task:
            await self._accepter_task.cancel(exc=ClosedResourceError)

    async def accept(self):
        await check_cancelled()
        if self.raw_socket.fileno() < 0:
            raise ClosedResourceError
        if self._accepter_task:
            raise BusyResourceError('accepting connections from')

        self._accepter_task = await curio.current_task()
        try:
            curio_socket, address = await self._curio_socket.accept()
        finally:
            self._accepter_task = None

        return self.stream_class(raw_socket=curio_socket._socket, curio_socket=curio_socket)


@attr.s(slots=True, auto_attribs=True)
class TCPSocketStream(StreamMixin, AbstractTCPSocketStream):
    async def send_eof(self) -> None:
        self.raw_socket.shutdown(socket.SHUT_WR)


async def connect_tcp(raw_socket: socket.SocketType, remote_address) -> AbstractTCPSocketStream:
    curio_socket = curio.io.Socket(raw_socket)
    await curio_socket.connect(remote_address)
    return TCPSocketStream(raw_socket=raw_socket, curio_socket=curio_socket)


class TCPListener(ListenerMixin, AbstractTCPListener):
    stream_class = TCPSocketStream


async def create_tcp_listener(raw_socket: socket.SocketType) -> AbstractTCPListener:
    curio_socket = curio.io.Socket(raw_socket)
    return TCPListener(raw_socket=raw_socket, curio_socket=curio_socket)


@attr.s(slots=True, auto_attribs=True)
class UNIXSocketStream(StreamMixin, AbstractUNIXSocketStream):
    async def send_eof(self) -> None:
        self.raw_socket.shutdown(socket.SHUT_WR)


async def connect_unix(raw_socket: socket.SocketType,
                       remote_address: str) -> AbstractUNIXSocketStream:
    curio_socket = curio.io.Socket(raw_socket)
    await curio_socket.connect(remote_address)
    return UNIXSocketStream(raw_socket=raw_socket, curio_socket=curio_socket)


class UNIXListener(ListenerMixin, AbstractUNIXListener):
    stream_class = UNIXSocketStream


async def create_unix_listener(raw_socket: socket.SocketType) -> AbstractUNIXListener:
    curio_socket = curio.io.Socket(raw_socket)
    return UNIXListener(raw_socket=raw_socket, curio_socket=curio_socket)


class UDPSocket(AbstractUDPSocket):
    receiver_task: Optional[curio.Task] = None
    sender_task: Optional[curio.Task] = None

    def __init__(self, sock: socket.SocketType):
        self.raw_socket = sock
        self._curio_socket = curio.io.Socket(sock)

    async def aclose(self) -> None:
        await self._curio_socket.close()
        if self.receiver_task:
            await self.receiver_task.cancel(exc=ClosedResourceError, blocking=False)
        if self.sender_task:
            await self.sender_task.cancel(exc=ClosedResourceError, blocking=False)

    async def receive(self) -> UDPPacket:
        await check_cancelled()
        if self.raw_socket.fileno() < 0:
            raise ClosedResourceError
        if self.receiver_task:
            raise BusyResourceError('receiving from')

        self.receiver_task = await curio.current_task()
        try:
            return UDPPacket(*await self._curio_socket.recvfrom(65536))
        finally:
            del self.receiver_task

    async def send(self, item: UDPPacket) -> None:
        await check_cancelled()
        if self.raw_socket.fileno() < 0:
            raise ClosedResourceError
        if self.sender_task:
            raise BusyResourceError('sending from')

        self.sender_task = await curio.current_task()
        try:
            await self._curio_socket.sendto(*item)
        finally:
            del self.sender_task


class ConnectedUDPSocket(AbstractConnectedUDPSocket):
    _receiver_task: Optional[curio.Task] = None
    _sender_task: Optional[curio.Task] = None

    def __init__(self, sock: socket.SocketType):
        self.raw_socket = sock
        self._curio_socket = curio.io.Socket(sock)

    async def aclose(self) -> None:
        await self._curio_socket.close()
        if self._receiver_task:
            await self._receiver_task.cancel(exc=ClosedResourceError, blocking=False)
        if self._sender_task:
            await self._sender_task.cancel(exc=ClosedResourceError, blocking=False)

    async def receive(self) -> bytes:
        await check_cancelled()
        if self.raw_socket.fileno() < 0:
            raise ClosedResourceError
        if self._receiver_task:
            raise BusyResourceError('receiving from')

        self._receiver_task = await curio.current_task()
        try:
            return await self._curio_socket.recv(65536)
        finally:
            del self._receiver_task

    async def send(self, item: bytes) -> None:
        await check_cancelled()
        if self.raw_socket.fileno() < 0:
            raise ClosedResourceError
        if self._sender_task:
            raise BusyResourceError('sending from')

        self._sender_task = await curio.current_task()
        try:
            await self._curio_socket.send(item)
        finally:
            del self._sender_task


async def create_udp_socket(raw_socket: socket.SocketType):
    try:
        raw_socket.getpeername()
    except OSError:
        return UDPSocket(raw_socket)
    else:
        return ConnectedUDPSocket(raw_socket)


#
# Synchronization
#

class Lock(curio.Lock):
    async def __aenter__(self):
        await check_cancelled()
        return await super().__aenter__()


class Condition(curio.Condition):
    async def __aenter__(self):
        await check_cancelled()
        return await super().__aenter__()

    async def wait(self):
        await check_cancelled()
        return await super().wait()


class Event(curio.Event):
    async def wait(self):
        await check_cancelled()
        return await super().wait()


class Semaphore(curio.Semaphore):
    async def __aenter__(self):
        await check_cancelled()
        return await super().__aenter__()


@attr.s(auto_attribs=True, slots=True)
class MemoryMessageStream(Generic[T_Item], MessageStream[T_Item]):
    _closed: bool = attr.ib(init=False, default=False)
    _send_event: curio.Event = attr.ib(init=False, factory=curio.Event)
    _receive_events: Dict[curio.Event, List[T_Item]] = attr.ib(init=False, factory=OrderedDict)

    async def receive(self) -> T_Item:
        await check_cancelled()
        if self._closed:
            raise ClosedResourceError

        event = curio.Event()
        container: List[T_Item] = []
        self._receive_events[event] = container
        await self._send_event.set()
        try:
            await event.wait()
        except BaseException:
            self._receive_events.pop(event, None)
            raise

        try:
            return container[0]
        except IndexError:
            raise ClosedResourceError from None

    async def send(self, item: T_Item) -> None:
        await check_cancelled()
        if self._closed:
            raise ClosedResourceError

        # Wait until there's someone on the receiving end
        if not self._receive_events:
            self._send_event.clear()
            await self._send_event.wait()

        # Send the item to the first recipient in the line
        event, container = self._receive_events.popitem()
        container.append(item)
        await event.set()

    async def aclose(self) -> None:
        self._closed = True
        for event in self._receive_events:
            await event.set()


class CapacityLimiter:
    def __init__(self, total_tokens: float):
        self._set_total_tokens(total_tokens)
        self._borrowers = set()  # type: Set[Any]
        self._wait_queue = OrderedDict()  # type: Dict[Any, curio.Event]

    async def __aenter__(self):
        await self.acquire()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.release()

    def _set_total_tokens(self, value: float) -> None:
        if not isinstance(value, int) and not math.isinf(value):
            raise TypeError('total_tokens must be an int or math.inf')
        if value < 1:
            raise ValueError('total_tokens must be >= 1')

        self._total_tokens = value

    @property
    def total_tokens(self) -> float:
        return self._total_tokens

    async def set_total_tokens(self, value: float) -> None:
        old_value = self._total_tokens
        self._set_total_tokens(value)
        events = []
        for event in self._wait_queue.values():
            if value <= old_value:
                break

            if not event.is_set():
                events.append(event)
                old_value += 1

        for event in events:
            await event.set()

    @property
    def borrowed_tokens(self) -> int:
        return len(self._borrowers)

    @property
    def available_tokens(self) -> float:
        return self._total_tokens - len(self._borrowers)

    async def acquire_nowait(self):
        await self.acquire_on_behalf_of_nowait(await curio.current_task())

    async def acquire_on_behalf_of_nowait(self, borrower):
        if borrower in self._borrowers:
            raise RuntimeError("this borrower is already holding one of this CapacityLimiter's "
                               "tokens")

        if self._wait_queue or len(self._borrowers) >= self._total_tokens:
            raise WouldBlock

        self._borrowers.add(borrower)

    async def acquire(self):
        return await self.acquire_on_behalf_of(await curio.current_task())

    async def acquire_on_behalf_of(self, borrower):
        try:
            await self.acquire_on_behalf_of_nowait(borrower)
        except WouldBlock:
            event = curio.Event()
            self._wait_queue[borrower] = event
            try:
                await event.wait()
            except BaseException:
                self._wait_queue.pop(borrower, None)
                raise

            self._borrowers.add(borrower)

    async def release(self):
        await self.release_on_behalf_of(await curio.current_task())

    async def release_on_behalf_of(self, borrower):
        try:
            self._borrowers.remove(borrower)
        except KeyError:
            raise RuntimeError("this borrower isn't holding any of this CapacityLimiter's "
                               "tokens") from None

        # Notify the next task in line if this limiter has free capacity now
        if self._wait_queue and len(self._borrowers) < self._total_tokens:
            event = self._wait_queue.popitem()[1]
            await event.set()


def current_default_thread_limiter():
    return _default_thread_limiter


_default_thread_limiter = CapacityLimiter(40)

AbstractLock.register(Lock)
AbstractCondition.register(Condition)
AbstractEvent.register(Event)
AbstractSemaphore.register(Semaphore)
AbstractCapacityLimiter.register(CapacityLimiter)


#
# Operating system signals
#

@asynccontextmanager
async def receive_signals(*signals: int):
    with curio.SignalQueue(*signals) as queue:
        yield queue


#
# Testing and debugging
#

async def get_current_task() -> TaskInfo:
    task = await curio.current_task()
    return TaskInfo(task.id, task.parentid, task.name, task.coro)


async def get_running_tasks() -> List[TaskInfo]:
    task_infos = []
    kernel = await curio.traps._get_kernel()
    for task in kernel._tasks.values():
        if not task.terminated:
            task_infos.append(TaskInfo(task.id, task.parentid, task.name, task.coro))

    return task_infos


async def wait_all_tasks_blocked() -> None:
    this_task = await curio.current_task()
    kernel = await curio.traps._get_kernel()
    while True:
        for task in kernel._tasks.values():
            if task.id != this_task.id and task.coro.cr_await is None:
                await curio.sleep(0)
                break
        else:
            return
