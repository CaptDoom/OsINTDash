"""
Drishya Network Safety Utilities
================================
Shared SSRF protection and URL validation used across all routes.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import urllib.parse
from typing import Optional

logger = logging.getLogger("drishya.net_safety")


def is_safe_url(url: str, allowed_schemes: tuple = ("http", "https")) -> bool:
    """
    Validate that a URL does not point to private/loopback/link-local addresses.
    Prevents SSRF attacks against cloud IMDS, internal services, and localhost.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in allowed_schemes:
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        # Resolve hostname and check all resulting IPs
        for _, _, _, _, sockaddr in socket.getaddrinfo(hostname, None):
            ip = ipaddress.ip_address(sockaddr[0])
            if (
                ip.is_loopback
                or ip.is_private
                or ip.is_link_local
                or ip.is_unspecified
                or ip.is_reserved
            ):
                return False
        return True
    except Exception:
        return False


def sanitize_filename(filename: str) -> str:
    """
    Strip path components from a filename to prevent path traversal attacks.
    Returns only the final basename segment.
    """
    from pathlib import Path
    return Path(filename or "upload").name


def is_allowed_upload_extension(filename: str, allowed: Optional[set] = None) -> bool:
    """Check if a file extension is in the allowed set."""
    if allowed is None:
        allowed = {".pdf", ".docx", ".doc", ".txt", ".md"}
    from pathlib import Path
    ext = Path(filename or "").suffix.lower()
    return ext in allowed
