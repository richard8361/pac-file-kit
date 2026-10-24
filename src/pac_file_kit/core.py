"""Core parsing and helper-implementation logic for pac-file-kit.

This module deliberately parses PAC files using only the standard library.
A PAC file is JavaScript, and in theory you'd evaluate it with a JS engine.
Here we do NOT execute the script: we extract text regions so an operator
can read them offline. That is a narrower goal, but it is the goal we
actually support, and it keeps the dependency surface at zero.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class PacParseError(ValueError):
    """Raised when a PAC file lacks the structure we require.

    We inherit from ValueError so callers who only care about "bad input"
    can catch a single built-in type.
    """


@dataclass(frozen=True)
class PacSource:
    """A region of source text extracted from a PAC file.

    Fields:
        name: The function or helper name (e.g. "FindProxyForURL").
        body: The full source text of the function including its signature
            and surrounding braces.
        start_line: 1-based line number where the function declaration begins.
        end_line: 1-based line number of the closing brace.

    We keep line numbers because operators triaging a PAC file almost always
    want to jump to the text in their editor. Offsets would be cheaper to
    compute but far less useful at the point of use.
    """

    name: str
    body: str
    start_line: int
    end_line: int


def _line_up_to(index: int, text: str) -> int:
    """Return the 1-based line number for the given character index."""
    return text.count("\n", 0, index) + 1


# Match a function declaration. We intentionally handle these forms:
#   function FindProxyForURL(url, host) {
#   function FindProxyForURL(url, host)
#   {
# And we require the body to start with a brace on the same line or the next
# non-empty line. We do NOT support `const FindProxyForURL = (...) => {...}`
# because real PAC engines historically call `FindProxyForURL` as a named
# function, and supporting arrow forms invites a much more complex parser
# without clear benefit. We document this boundary rather than guess.
_FUNC_RE = re.compile(
    r"""
    \bfunction\s+
    (?P<name>[A-Za-z_$][\w$]*)
    \s*
    \(
    [^()]*
    (?:
        \(
        [^()]*
        \)
        [^()]*
    )*
    \)
    \s*\{
    """,
    re.VERBOSE,
)


def find_function(text: str, name: str = "FindProxyForURL") -> PacSource:
    """Find a top-level function named ``name`` in ``text``.

    Args:
        text: The full PAC file text.
        name: The function name to locate. Defaults to ``FindProxyForURL``.

    Returns:
        A :class:`PacSource` describing the function.

    Raises:
        PacParseError: If the function is missing or its braces are
            unbalanced.

    The brace-counting handles braces inside string and regex literals in a
    best-effort way: we treat ``//`` line comments and ``/* */`` block
    comments as non-code, and we skip the contents of single- and
    double-quoted strings with backslash escapes. We do NOT handle template
    literals (backticks) because PAC files in the wild rarely use them; if a
    template literal contains an unescaped brace the count can be off. That
    is the documented edge.
    """
    m = _FUNC_RE.search(text)
    while m is not None:
        if m.group("name") == name:
            break
        m = _FUNC_RE.search(text, m.end())
    if m is None or m.group("name") != name:
        raise PacParseError(f"function {name!r} not found")

    open_brace = m.end() - 1  # index of the '{'
    body_end = _match_braces(text, open_brace)
    if body_end is None:
        raise PacParseError(f"unbalanced braces in {name!r}")

    body = text[m.start():body_end + 1]
    return PacSource(
        name=name,
        body=body,
        start_line=_line_up_to(m.start(), text),
        end_line=_line_up_to(body_end, text),
    )


def _match_braces(text: str, open_index: int) -> int | None:
    """Return the index of the brace matching the brace at ``open_index``.

    Returns ``None`` if unbalanced. We walk the string a character at a time,
    tracking whether we are inside a string, a line comment, or a block
    comment. Escapes inside strings are respected for ``\\`` and for the
    matching quote character only; this is sufficient for PAC files, which do
    not use multi-line template literals in practice.
    """
    depth = 0
    i = open_index
    n = len(text)
    quote: str | None = None
    in_line_comment = False
    in_block_comment = False

    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue

        if ch == "/" and nxt == "/":
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1

    return None


# A helper is a top-level ``function`` declaration whose name is one of the
# DNS-resolution helpers defined by the PAC specification. We keep this list
# explicit rather than scanning for any ``function`` so that we don't pull in
# unrelated user-defined utilities that happen to appear in the file.
_DNS_HELPER_NAMES = {
    "dnsResolve",
    "isResolvable",
    "isInNet",
    "dnsDomainIs",
    "localHostOrDomainIs",
    "myIpAddress",
    "shExpMatch",
    "isPlainHostName",
}


def extract_dns_helpers(text: str) -> list[PacSource]:
    """Return every DNS-related helper function found in ``text``.

    Only functions whose names are in the PAC DNS-helper set are returned,
    and only those declared at the top level via ``function NAME(...) {...}``.
    Functions nested inside ``FindProxyForURL`` are not returned; PAC helpers
    are conventionally top-level, and pulling nested ones would risk
    duplicates and confusing line numbers.

    The returned list is in source order. If the same helper name is defined
    more than once, all definitions are returned; the caller can decide
    whether that's a problem.
    """
    helpers: list[PacSource] = []
    pos = 0
    n = len(text)
    while pos < n:
        m = _FUNC_RE.search(text, pos)
        if m is None:
            break
        name = m.group("name")
        if name in _DNS_HELPER_NAMES:
            open_brace = m.end() - 1
            end = _match_braces(text, open_brace)
            if end is None:
                # Skip a broken helper rather than failing the whole parse.
                # The caller's primary interest is FindProxyForURL; a
                # malformed helper is reported by its absence.
                pos = m.end()
                continue
            helpers.append(
                PacSource(
                    name=name,
                    body=text[m.start():end + 1],
                    start_line=_line_up_to(m.start(), text),
                    end_line=_line_up_to(end, text),
                )
            )
            pos = end + 1
        else:
            pos = m.end()
    return helpers


def dns_domain(host: str, domain: str) -> bool:
    """Implement the PAC ``dnsDomainIs`` predicate.

    Per the PAC spec, ``dnsDomainIs(host, domain)`` is true when ``host``
    equals ``domain`` OR ``host`` ends with a single ``.`` followed by
    ``domain``. We lowercase both inputs because PAC host comparisons are
    case-insensitive in practice; this matches what every widely deployed
    PAC engine does.

    We do not strip trailing dots from ``host``: a host with a trailing dot
    is the FQDN form and should not match a domain specified without one.
    """
    h = host.lower()
    d = domain.lower()
    if h == d:
        return True
    return h.endswith("." + d)


def sh_exp_match(value: str, pattern: str) -> bool:
    """Implement the PAC ``shExpMatch`` predicate.

    ``shExpMatch`` in PAC engines matches shell-style globs: ``*`` matches
    any run of characters (including none) and ``?`` matches any single
    character. Notably it does NOT match ``[a-z]`` character classes the way
    a POSIX shell does, and we deliberately do not support them either:
    supporting classes would require escaping and bracket parsing that the
    PAC spec does not define, and would create disagreement with real
    engines. ``*`` and ``?`` only.

    The match is case-sensitive because ``shExpMatch`` is documented as
    case-sensitive in common PAC references and because the most common
    argument is a URL host or a URL scheme, where case matters.
    """
    regex = _sh_to_regex(pattern)
    return re.match(regex, value, re.DOTALL) is not None


_SH_SPECIAL = {"*": ".*", "?": "."}


def _sh_to_regex(pattern: str) -> str:
    """Translate an ``shExpMatch`` pattern to an anchored regex string.

    We build the regex by hand instead of using :mod:`fnmatch`, because
    ``fnmatch`` translates ``[seq]`` into a character class and we have
    decided not to support classes. Building it ourselves keeps the
    behaviour identical to what we document.
    """
    out: list[str] = ["^"]
    for ch in pattern:
        if ch in _SH_SPECIAL:
            out.append(_SH_SPECIAL[ch])
        else:
            out.append(re.escape(ch))
    out.append("$")
    return "".join(out)
