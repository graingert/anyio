from typing import AsyncContextManager, Coroutine, Optional

from .._core._eventloop import get_asynclib
from ._compat import DeprecatedAwaitable, DeprecatedAwaitableList


class TaskInfo(DeprecatedAwaitable):
    """
    Represents an asynchronous task.

    :ivar int id: the unique identifier of the task
    :ivar parent_id: the identifier of the parent task, if any
    :vartype parent_id: Optional[int]
    :ivar str name: the description of the task (if any)
    :ivar ~collections.abc.Coroutine coro: the coroutine object of the task
    """

    __slots__ = 'id', 'parent_id', 'name', 'coro'

    def __init__(self, id: int, parent_id: Optional[int], name: Optional[str], coro: Coroutine):
        super().__init__(get_current_task)
        self.id = id
        self.parent_id = parent_id
        self.name = name
        self.coro = coro

    def __await__(self):
        yield from super().__await__()
        return self

    def __eq__(self, other):
        if isinstance(other, TaskInfo):
            return self.id == other.id

        return NotImplemented

    def __hash__(self):
        return hash(self.id)

    def __repr__(self):
        return f'{self.__class__.__name__}(id={self.id!r}, name={self.name!r})'


def get_current_task() -> TaskInfo:
    """
    Return the current task.

    :return: a representation of the current task

    """
    return get_asynclib().get_current_task()


def get_running_tasks() -> DeprecatedAwaitableList[TaskInfo]:
    """
    Return a list of running tasks in the current event loop.

    :return: a list of task info objects

    """
    tasks = get_asynclib().get_running_tasks()
    return DeprecatedAwaitableList(tasks, func=get_running_tasks)


async def wait_all_tasks_blocked() -> None:
    """Wait until all other tasks are waiting for something."""
    await get_asynclib().wait_all_tasks_blocked()


class Sequencer:
    """A convenience class for forcing code in different tasks to run in an
    explicit linear order.
    Instances of this class implement a ``__call__`` method which returns an
    async context manager. The idea is that you pass a sequence number to
    ``__call__`` to say where this block of code should go in the linear
    sequence. Block 0 starts immediately, and then block N doesn't start until
    block N-1 has finished.
    Example:
      An extremely elaborate way to print the numbers 0-5, in order::
         async def worker1(seq):
             async with seq(0):
                 print(0)
             async with seq(4):
                 print(4)
         async def worker2(seq):
             async with seq(2):
                 print(2)
             async with seq(5):
                 print(5)
         async def worker3(seq):
             async with seq(1):
                 print(1)
             async with seq(3):
                 print(3)
         async def main():
            seq = anyio.testing.Sequencer()
            async with anyio.create_task_group() as task_group:
                task_group.start_soon(worker1, seq)
                task_group.start_soon(worker2, seq)
                task_group.start_soon(worker3, seq)
    """

    def __new__(cls):
        return get_asynclib().Sequencer()

    def __call__(self, position: int) -> AsyncContextManager[None]:
        raise NotImplementedError
