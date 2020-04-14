from abc import abstractmethod
from io import SEEK_SET
from typing import Union, Optional

from . import AsyncResource


class AsyncFile(AsyncResource):
    """
    An asynchronous file object.

    This class wraps a standard file object and provides async friendly versions of the following
    blocking methods (where available on the original file object):

    * read
    * read1
    * readline
    * readlines
    * readinto
    * readinto1
    * write
    * writelines
    * truncate
    * seek
    * tell
    * flush
    * close

    All other methods are directly passed through.

    This class supports the asynchronous context manager protocol which closes the underlying file
    at the end of the context block.

    This class also supports asynchronous iteration::

        async with await anyio.open_file(...) as f:
            async for line in f:
                print(line)
    """

    @property
    @abstractmethod
    def wrapped(self):
        """The wrapped synchronous file object."""

    @abstractmethod
    def __aiter__(self):
        pass

    @abstractmethod
    async def read(self, size: int = -1) -> Union[bytes, str]:
        pass

    @abstractmethod
    async def read1(self, size: int = -1) -> Union[bytes, str]:
        pass

    @abstractmethod
    async def readline(self) -> bytes:
        pass

    @abstractmethod
    async def readlines(self) -> bytes:
        pass

    @abstractmethod
    async def readinto(self, b: Union[bytes, memoryview]) -> bytes:
        pass

    @abstractmethod
    async def readinto1(self, b: Union[bytes, memoryview]) -> bytes:
        pass

    @abstractmethod
    async def write(self, b: bytes) -> None:
        pass

    @abstractmethod
    async def writelines(self, lines: bytes) -> None:
        pass

    @abstractmethod
    async def truncate(self, size: Optional[int] = None) -> int:
        pass

    @abstractmethod
    async def seek(self, offset: int, whence: Optional[int] = SEEK_SET) -> int:
        pass

    @abstractmethod
    async def tell(self) -> int:
        pass

    @abstractmethod
    async def flush(self) -> None:
        pass
