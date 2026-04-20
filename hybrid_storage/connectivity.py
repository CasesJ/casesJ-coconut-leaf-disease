"""
Internet Connectivity Checker

Detects if the system has internet connectivity by attempting
to reach multiple DNS servers and external hosts.
"""

import socket
import asyncio
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)


class ConnectivityChecker:
    """Handles internet connectivity detection with fallback DNS servers"""

    # Primary DNS servers and hosts to check
    DNS_SERVERS = [
        ("8.8.8.8", 53),           # Google DNS
        ("1.1.1.1", 53),           # Cloudflare DNS
        ("208.67.222.222", 53),    # OpenDNS
    ]

    # HTTP endpoints for connectivity check (timeout quickly)
    HTTP_HOSTS = [
        "http://google.com",
        "http://cloudflare.com",
        "http://1.1.1.1",
    ]

    @staticmethod
    def check_dns_connectivity(timeout: int = 3) -> bool:
        """
        Check connectivity by attempting DNS resolution
        
        Args:
            timeout: Socket timeout in seconds
            
        Returns:
            True if at least one DNS server is reachable
        """
        for dns_host, dns_port in ConnectivityChecker.DNS_SERVERS:
            try:
                socket.create_connection((dns_host, dns_port), timeout=timeout)
                logger.info(f"✅ DNS connectivity verified via {dns_host}")
                return True
            except (socket.timeout, socket.gaierror, OSError):
                continue

        logger.warning("❌ No DNS servers reachable")
        return False

    @staticmethod
    async def check_internet_async(timeout: int = 5) -> bool:
        """
        Async check for internet connectivity
        
        Args:
            timeout: Connection timeout in seconds
            
        Returns:
            True if internet is available
        """
        loop = asyncio.get_event_loop()
        
        # Check DNS in thread pool to avoid blocking
        is_connected = await loop.run_in_executor(
            None,
            ConnectivityChecker.check_dns_connectivity,
            timeout
        )
        
        return is_connected

    @staticmethod
    def check_internet_sync(timeout: int = 3) -> bool:
        """
        Synchronous check for internet connectivity (default)
        
        Args:
            timeout: Connection timeout in seconds
            
        Returns:
            True if internet is available
        """
        return ConnectivityChecker.check_dns_connectivity(timeout)


async def check_internet_connectivity(timeout: int = 3) -> Dict[str, Any]:
    """
    Check current internet connectivity status
    
    Returns:
        {
            "is_connected": bool,
            "timestamp": ISO timestamp,
            "method": "DNS" or "socket"
        }
    """
    from datetime import datetime
    
    try:
        is_connected = await ConnectivityChecker.check_internet_async(timeout)
        return {
            "is_connected": is_connected,
            "timestamp": datetime.utcnow().isoformat(),
            "method": "DNS",
        }
    except Exception as e:
        logger.error(f"Error checking connectivity: {e}")
        return {
            "is_connected": False,
            "timestamp": datetime.utcnow().isoformat(),
            "method": "error",
            "error": str(e),
        }


def check_internet_connectivity_sync(timeout: int = 3) -> Dict[str, Any]:
    """
    Synchronous version of check_internet_connectivity
    Useful for non-async contexts
    """
    from datetime import datetime
    
    try:
        is_connected = ConnectivityChecker.check_internet_sync(timeout)
        return {
            "is_connected": is_connected,
            "timestamp": datetime.utcnow().isoformat(),
            "method": "DNS",
        }
    except Exception as e:
        logger.error(f"Error checking connectivity: {e}")
        return {
            "is_connected": False,
            "timestamp": datetime.utcnow().isoformat(),
            "method": "error",
            "error": str(e),
        }
