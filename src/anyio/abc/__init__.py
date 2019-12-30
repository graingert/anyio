from abc import ABCMeta, abstractmethod


class AsyncResource(metaclass=ABCMeta):
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        await self.aclose()

    @abstractmethod
    async def aclose(self) -> None:
        """
        Close the stream.

        This method may cause some cleanup actions to be taken (e.g. the closing handshake of a
        TLS connection). If this method is interrupted, it means that the resource will still be
        closed, but uncleanly.
        """
