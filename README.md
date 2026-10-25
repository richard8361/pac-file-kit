# pac-file-kit

`pac-file-kit` extracts the `FindProxyForURL` function and the standard PAC
DNS helper functions from a PAC file's text so you can inspect them offline,
without a JavaScript engine. It also ships pure-Python implementations of the
`dnsDomainIs` and `shExpMatch` predicates so you can reason about what those
helpers would return.

## Usage

```python
from pac_file_kit import (
    find_function,
    extract_dns_helpers,
    dns_domain,
    sh_exp_match,
)

pac_text = "function FindProxyForURL(url, host) { return 'DIRECT'; }"

main = find_function(pac_text)
print(main.start_line, main.end_line)
print(main.body)

for helper in extract_dns_helpers(pac_text):
    print(helper.name, helper.start_line)
    print(helper.body)

print(dns_domain("www.corp.example", "corp.example"))
print(sh_exp_match("https://intranet", "https://*"))
```

Run the tests:

```
PYTHONPATH=src python -m unittest discover -s tests
```

## Why this exists

When a corporate PAC file misbehaves, the first step is usually to read what
`FindProxyForURL` is actually doing and to check which DNS helpers it relies
on. That shouldn't require a JS runtime, a container image, or network
access. `pac-file-kit` does the parse with the Python standard library only:
no `node`, no `js2py`, no pip install. The trade-off is that the library
does not execute the PAC file. It extracts source regions and implements the
small set of pure predicates (`dnsDomainIs`, `shExpMatch`) that are useful to
reason about offline. If you need to evaluate `FindProxyForURL` against a
live URL, use a JS engine — that is deliberately out of scope here.

## Exported names

- `find_function(text, name="FindProxyForURL")` returns a `PacSource` with
  `name`, `body`, `start_line`, and `end_line`.
- `extract_dns_helpers(text)` returns a list of `PacSource` objects, one per
  standard DNS helper (`dnsResolve`, `isResolvable`, `isInNet`, `dnsDomainIs`,
  `localHostOrDomainIs`, `myIpAddress`, `shExpMatch`, `isPlainHostName`) found
  at the top level of the file.
- `dns_domain(host, domain)` implements the `dnsDomainIs` predicate.
- `sh_exp_match(value, pattern)` implements the `shExpMatch` predicate.
- `PacSource` is the dataclass returned by the extractors.
- `PacParseError` is raised when `find_function` cannot locate the function or
  its braces are unbalanced.

## The awkward edge

Brace matching handles braces inside single- and double-quoted strings, line
comments (`//`), and block comments (`/* */`) with backslash escapes inside
strings. It does **not** handle template literals (backticks). PAC files in
the wild almost never use them, but if one does contain a backtick string
with an unescaped `}`, the extracted `body` may be truncated or run past the
intended end. This is a documented boundary, not a bug to work around without
a real test corpus. If you hit it, pre-process the file or open an issue with
the PAC text that triggers it.

`shExpMatch` supports only `*` and `?`. It deliberately does not interpret
`[a-z]` as a character class: the PAC references do not define that, and
real engines disagree. Brackets are matched literally.
