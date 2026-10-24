"""pac-file-kit: extract FindProxyForURL and DNS helpers from a PAC file for offline inspection."""

from .core import PacSource, PacParseError, find_function, extract_dns_helpers, dns_domain, sh_exp_match

__all__ = [
    "PacSource",
    "PacParseError",
    "find_function",
    "extract_dns_helpers",
    "dns_domain",
    "sh_exp_match",
]
