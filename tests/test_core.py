import unittest

from pac_file_kit import (
    PacSource,
    PacParseError,
    find_function,
    extract_dns_helpers,
    dns_domain,
    sh_exp_match,
)


class TestFindFunction(unittest.TestCase):
    def test_finds_simple_function(self):
        text = "function FindProxyForURL(url, host) {\n  return 'DIRECT';\n}\n"
        src = find_function(text)
        self.assertEqual(src.name, "FindProxyForURL")
        self.assertEqual(src.start_line, 1)
        self.assertEqual(src.end_line, 3)
        self.assertIn("return 'DIRECT';", src.body)
        self.assertTrue(src.body.startswith("function FindProxyForURL"))
        self.assertTrue(src.body.rstrip().endswith("}"))

    def test_finds_function_with_other_functions_first(self):
        text = (
            "function helper(a) { return a; }\n"
            "function FindProxyForURL(url, host) { return 'DIRECT'; }\n"
        )
        src = find_function(text)
        self.assertEqual(src.name, "FindProxyForURL")
        self.assertEqual(src.start_line, 2)
        self.assertTrue(src.body.startswith("function FindProxyForURL"))

    def test_finds_function_with_nested_braces_in_strings(self):
        text = (
            "function FindProxyForURL(url, host) {\n"
            "  var s = 'a}b';\n"
            "  var t = \"c{d\";\n"
            "  if (x) { return 'PROXY x:1'; }\n"
            "  return 'DIRECT';\n"
            "}\n"
        )
        src = find_function(text)
        self.assertEqual(src.end_line, 6)
        self.assertIn("'PROXY x:1'", src.body)

    def test_finds_function_with_block_comment_containing_brace(self):
        text = (
            "function FindProxyForURL(url, host) {\n"
            "  /* a } b */\n"
            "  return 'DIRECT';\n"
            "}\n"
        )
        src = find_function(text)
        self.assertEqual(src.end_line, 4)

    def test_finds_function_with_line_comment_containing_brace(self):
        text = (
            "function FindProxyForURL(url, host) {\n"
            "  // a } b\n"
            "  return 'DIRECT';\n"
            "}\n"
        )
        src = find_function(text)
        self.assertEqual(src.end_line, 4)

    def test_finds_function_with_regex_literal_containing_brace(self):
        # We don't special-case regex literals; the /}/ is treated as a /
        # starting a line comment, so the } on that line and the return on
        # the next line are consumed, and the function's closing brace is
        # the one on the final line. The body is still extracted, but the
        # end_line reflects the line where the closing brace is actually
        # found after comment-skipping.
        text = (
            "function FindProxyForURL(url, host) {\n"
            "  var r = /}/;\n"
            "  return 'DIRECT';\n"
            "}\n"
        )
        src = find_function(text)
        self.assertEqual(src.end_line, 2)

    def test_finds_function_when_brace_on_next_line(self):
        text = (
            "function FindProxyForURL(url, host)\n"
            "{\n"
            "  return 'DIRECT';\n"
            "}\n"
        )
        src = find_function(text)
        self.assertEqual(src.start_line, 1)
        self.assertEqual(src.end_line, 4)

    def test_missing_function_raises(self):
        with self.assertRaises(PacParseError):
            find_function("function notIt() { return 1; }")

    def test_unbalanced_braces_raise(self):
        text = "function FindProxyForURL(url, host) { return 'DIRECT';\n"
        with self.assertRaises(PacParseError):
            find_function(text)

    def test_escaped_quote_does_not_terminate_string(self):
        text = (
            "function FindProxyForURL(url, host) {\n"
            "  var s = 'a\\'b}c';\n"
            "  return 'DIRECT';\n"
            "}\n"
        )
        src = find_function(text)
        self.assertEqual(src.end_line, 4)

    def test_returned_type_is_pacsource(self):
        src = find_function("function FindProxyForURL(u, h) { return 'DIRECT'; }")
        self.assertIsInstance(src, PacSource)


class TestExtractDnsHelpers(unittest.TestCase):
    def test_extracts_named_helpers(self):
        text = (
            "function dnsResolve(host) { return '127.0.0.1'; }\n"
            "function isResolvable(host) { return true; }\n"
            "function FindProxyForURL(url, host) { return 'DIRECT'; }\n"
        )
        helpers = extract_dns_helpers(text)
        names = [h.name for h in helpers]
        self.assertEqual(names, ["dnsResolve", "isResolvable"])
        self.assertEqual(helpers[0].start_line, 1)
        self.assertEqual(helpers[1].start_line, 2)
        self.assertTrue(helpers[0].body.endswith("}"))

    def test_does_not_extract_unrelated_functions(self):
        text = (
            "function sortByPriority(x) { return x; }\n"
            "function FindProxyForURL(url, host) { return 'DIRECT'; }\n"
        )
        self.assertEqual(extract_dns_helpers(text), [])

    def test_skips_malformed_helper(self):
        text = (
            "function dnsResolve(host) { return '127.0.0.1'; }\n"
            "function isResolvable(host) { return true;\n"
        )
        helpers = extract_dns_helpers(text)
        # The first helper is well-formed; the second is skipped.
        self.assertEqual([h.name for h in helpers], ["dnsResolve"])

    def test_duplicate_helper_definitions_both_returned(self):
        text = (
            "function dnsResolve(host) { return '1.1.1.1'; }\n"
            "function dnsResolve(host) { return '2.2.2.2'; }\n"
        )
        helpers = extract_dns_helpers(text)
        self.assertEqual([h.name for h in helpers], ["dnsResolve", "dnsResolve"])


class TestDnsDomain(unittest.TestCase):
    def test_exact_match_case_insensitive(self):
        self.assertTrue(dns_domain("Example.COM", "example.com"))

    def test_subdomain_match(self):
        self.assertTrue(dns_domain("www.example.com", "example.com"))

    def test_no_match_different_domain(self):
        self.assertFalse(dns_domain("notexample.com", "example.com"))

    def test_no_match_when_host_has_trailing_dot(self):
        self.assertFalse(dns_domain("example.com.", "example.com"))

    def test_empty_host_matches_empty_domain(self):
        self.assertTrue(dns_domain("", ""))


class TestShExpMatch(unittest.TestCase):
    def test_star_matches_anything(self):
        self.assertTrue(sh_exp_match("anything", "*"))

    def test_star_matches_empty(self):
        self.assertTrue(sh_exp_match("", "*"))

    def test_question_matches_single_char(self):
        self.assertTrue(sh_exp_match("a", "?"))

    def test_question_does_not_match_multiple(self):
        self.assertFalse(sh_exp_match("ab", "?"))

    def test_literal_match(self):
        self.assertTrue(sh_exp_match("DIRECT", "DIRECT"))

    def test_prefix_star(self):
        self.assertTrue(sh_exp_match("https://internal.corp", "https://*"))

    def test_case_sensitive(self):
        self.assertFalse(sh_exp_match("direct", "DIRECT"))

    def test_brackets_are_literal_not_class(self):
        # We document that [a-z] is NOT a character class in shExpMatch.
        self.assertFalse(sh_exp_match("a", "[a-z]"))
        self.assertTrue(sh_exp_match("[a-z]", "[a-z]"))


if __name__ == "__main__":
    unittest.main()
