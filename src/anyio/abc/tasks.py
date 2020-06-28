import threading
from abc import ABCMeta, abstractmethod
from collections import Coroutine
from concurrent.futures import Future
from inspect import isawaitable
from types import TracebackType
from typing import Callable, Type, Optional, TypeVar

T_Retval = TypeVar('T_Retval')


class CancelScope(metaclass=ABCMeta):
    @abstractmethod
    async def __aenter__(self):
        pass

    @abstractmethod
    async def __aexit__(self, exc_type: Type[BaseException], exc_val: BaseException,
                        exc_tb: TracebackType):
        pass

    @abstractmethod
    async def cancel(self) -> None:
        """Cancel this scope immediately."""

    @property
    @abstractmethod
    def deadline(self) -> float:
        """
        The time (clock value) when this scope is cancelled automatically.

        Will be ``float('inf')``` if no timeout has been set.
        """

    @property
    @abstractmethod
    def cancel_called(self) -> bool:
        """``True`` if :meth:`cancel` has been called."""

    @property
    @abstractmethod
    def shield(self) -> bool:
        """
        ``True`` if this scope is shielded from external cancellation.

        While a scope is shielded, it will not receive cancellations from outside.
        """


class TaskGroup(metaclass=ABCMeta):
    """
    Groups several asynchronous tasks together.

    :ivar cancel_scope: the cancel scope inherited by all child tasks
    :vartype cancel_scope: CancelScope
    """

    cancel_scope: CancelScope

    @abstractmethod
    async def __aenter__(self) -> 'TaskGroup':
        """Enter the task group context and allow starting new tasks."""

    @abstractmethod
    async def __aexit__(self, exc_type: Type[BaseException], exc_val: BaseException,
                        exc_tb: TracebackType) -> Optional[bool]:
        """Exit the task group context waiting for all tasks to finish."""

    @abstractmethod
    async def spawn(self, func: Callable[..., Coroutine], *args, name=None) -> None:
        """
        Launch a new task in this task group.

        :param func: a coroutine function
        :param args: positional arguments to call the function with
        :param name: name of the task, for the purposes of introspection and debugging
        """


class BlockingPortal(metaclass=ABCMeta):
    """An object tied that lets external threads run code in an asynchronous event loop."""

    __slots__ = ('_task_group', '_event_loop_thread_id', '_stop_event', '_cancelled_exc_class')

    def __init__(self):
        from .. import create_event, create_task_group, get_cancelled_exc_class

        self._event_loop_thread_id = threading.get_ident()
        self._stop_event = create_event()
        self._task_group = create_task_group()
        self._cancelled_exc_class = get_cancelled_exc_class()

    async def __aenter__(self) -> 'BlockingPortal':
        await self._task_group.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop(cancel_remaining=exc_val is not None)
        return await self._task_group.__aexit__(exc_type, exc_val, exc_tb)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop_from_external_thread(cancel_remaining=exc_val is not None)

    async def sleep_until_stopped(self) -> None:
        """Sleep until :meth:`stop` is called."""
        await self._stop_event.wait()

    async def stop(self, cancel_remaining: bool = False) -> None:
        """
        Signal the portal to shut down.

        This marks the portal as no longer accepting new calls and exits from
        :meth:`sleep_until_stopped`.

        :param cancel_remaining: ``True`` to cancel all the remaining tasks, ``False`` to let them
            finish before returning

        """
        self._event_loop_thread_id = None
        await self._stop_event.set()
        if cancel_remaining:
            await self._task_group.cancel_scope.cancel()

    def stop_from_external_thread(self, cancel_remaining: bool = False) -> None:
        thread = self.call(threading.current_thread)
        self.call(self.stop, cancel_remaining)
        thread.join()

    async def _call_func(self, func: Callable, args: tuple, future: Future) -> None:
        try:
            retval = func(*args)
            if isawaitable(retval):
                future.set_result(await retval)
            else:
                future.set_result(retval)
        except self._cancelled_exc_class:
            future.cancel()
        except BaseException as exc:
            future.set_exception(exc)

    @abstractmethod
    def _spawn_task_from_thread(self, func: Callable, args: tuple, future: Future) -> None:
        pass

    def call(self, func: Callable[..., T_Retval], *args) -> T_Retval:
        """
        Call the given function in the event loop thread.

        If the callable returns a coroutine object, it is awaited on.

        :param func: any callable

        :raises RuntimeError: if this method is called from within the event loop thread

        """
        if self._event_loop_thread_id is None:
            raise RuntimeError('This portal is not running')
        if self._event_loop_thread_id == threading.get_ident():
            raise RuntimeError('This method cannot be called from the event loop thread')

        future: Future = Future()
        self._spawn_task_from_thread(func, args, future)
        return future.result()
