from abc import ABCMeta, abstractmethod
from typing import Optional

from anyio.abc.streams import SendByteStream, ReceiveByteStream


class AsyncProcess(metaclass=ABCMeta):
    """An asynchronous version of :class:`subprocess.Process`."""

    @abstractmethod
    async def wait(self) -> int:
        """
        Wait until the process exits.

        :return: the exit code of the process
        """

    @abstractmethod
    def terminate(self) -> None:
        """
        Terminates the process, gracefully if possible.

        On Windows, this calls ``TerminateProcess()``.
        On POSIX systems, this sends ``SIGTERM`` to the process.

        .. seealso:: :meth:`subprocess.Popen.terminate`
        """

    @abstractmethod
    def kill(self) -> None:
        """
        Kills the process.

        On Windows, this calls ``TerminateProcess()``.
        On POSIX systems, this sends ``SIGKILL`` to the process.

        .. seealso:: :meth:`subprocess.Popen.kill`
        """

    @abstractmethod
    def send_signal(self, signal: int) -> None:
        """
        Send a signal to the subprocess.

        .. seealso:: :meth:`subprocess.Popen.send_signal`

        :param signal: the signal number (e.g. :data:`signal.SIGHUP`)
        """

    @property
    @abstractmethod
    def pid(self) -> int:
        """The process ID of the process."""

    @property
    @abstractmethod
    def returncode(self) -> Optional[int]:
        """
        The return code of the process. If the process has not yet terminated, this will be
        ``None``.
        """

    @property
    @abstractmethod
    def stdin(self) -> Optional[SendByteStream]:
        """The stream for the standard input of the process."""

    @property
    @abstractmethod
    def stdout(self) -> Optional[ReceiveByteStream]:
        """The stream for the standard output of the process."""

    @property
    @abstractmethod
    def stderr(self) -> Optional[ReceiveByteStream]:
        """The stream for the standard error output of the process."""
