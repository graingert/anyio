import socket
import warnings
from ipaddress import ip_address, IPv6Address
from typing import Tuple, Optional

from .abc.networking import IPAddressType


async def get_bind_address(interface: Optional[IPAddressType]) -> Tuple[str, int, bool]:
    if interface:
        try:
            if_addr = ip_address(interface)
        except ValueError:
            from . import run_sync_in_worker_thread

            warnings.warn('Passing a host name as the interface address has been deprecated. '
                          'Use an IP address instead.', category=DeprecationWarning)
            res = await run_sync_in_worker_thread(socket.getaddrinfo, interface, 0)
            return res[0][-1][0], res[0][0], False

        family = socket.AF_INET6 if isinstance(if_addr, IPv6Address) else socket.AF_INET
        v6only = interface == '::'
        return str(if_addr), family, v6only
    elif socket.has_ipv6:
        return '::', socket.AF_INET6, False
    else:
        return '0.0.0.0', socket.AF_INET, False
