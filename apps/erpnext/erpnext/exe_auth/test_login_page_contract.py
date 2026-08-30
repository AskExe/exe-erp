"""
Login-page contract test (bug e1a9e4e9).

THE DEFECT
──────────
`frappe/www/login.html` ended its inline <script> with:

    document.body.innerHTML = '{% include "templates/includes/splash_screen.html" %}';

Jinja expands an `{% include %}` VERBATIM. `templates/includes/splash_screen.html`
is four lines of pretty-printed HTML, and a single-quoted JavaScript string
literal cannot contain a raw newline. So the page served to the browser carried
an UNTERMINATED STRING LITERAL, which is a parse-time SyntaxError, which kills
the ENTIRE <script> block — not just that statement.

Everything in that block died with it:

  * `ssoLink.href = '/api/method/erpnext.exe_auth.api.gotrue_login_start'`
    never ran, so "Sign in via Exe SSO" was an <a> with NO href — a dead
    control. Chromium on the live site reported `Invalid or unexpected token`
    and `#exe-sso-link` had `getAttribute('href') === null`.
  * the tab switcher never bound (Login / Login Token tabs inert),
  * the field-watcher that enables `.btn-login` never ran, so the password
    button stayed permanently `disabled`.

This is the failure mode the fork introduced for itself: upstream Frappe's
splash include was a single line, so embedding it in a single-quoted string
happened to work. Pretty-printing the include to four lines is harmless for
every other consumer (`www/desk.html` drops it straight into HTML,
`includes/login/login.js` wraps it in BACKTICKS) and fatal only here.

WHAT THIS FILE GUARDS
─────────────────────
1. The inline scripts of the login page must PARSE. Rather than trusting a
   reviewer to notice a quote character, the includes are expanded exactly as
   Jinja would and the result is scanned for a string literal left open at a
   line break. This is a real detector, not a spelling check: reverting the
   backticks in login.html to single quotes turns it red.
2. The SSO control must carry a REAL href in the server-rendered markup, so it
   survives the next time something in that script block breaks. A control
   whose only wiring is JavaScript has a single point of failure, and this bug
   is what that costs.

Deliberately frappe-free, like its sibling test_sso_cookie_contract.py, so it
runs under plain `python -m unittest` in CI with no bench and no live site.
See .github/scripts/ci_python_tests.py — a test module not listed there is a
test CI does not run.
"""

import os
import re
import unittest

# Repo root: .../apps/erpnext/erpnext/exe_auth/ -> up 4
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

LOGIN_TEMPLATE = os.path.join("frappe", "www", "login.html")
SPLASH_INCLUDE = os.path.join("frappe", "templates", "includes", "splash_screen.html")

# The server-side SSO entry point. The login control and the base.html Guest
# guard must both point HERE and never at gotrue_login_callback: only
# gotrue_login_start sets the exe_sso_state CSRF cookie the callback verifies.
SSO_START_PATH = "/api/method/erpnext.exe_auth.api.gotrue_login_start"

_INCLUDE_RE = re.compile(r"\{%-?\s*include\s+\"([^\"]+)\"[^%]*-?%\}")
# `{# ... #}` never reaches the browser, so it must be removed BEFORE the script
# blocks are located — otherwise prose that merely mentions a <script> tag would
# be scanned as if it were code.
_JINJA_COMMENT_RE = re.compile(r"\{#.*?#\}", re.DOTALL)
_JINJA_EXPR_RE = re.compile(r"\{\{.*?\}\}", re.DOTALL)
_JINJA_TAG_RE = re.compile(r"\{%.*?%\}", re.DOTALL)
_SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)


def _read(rel_path):
    with open(os.path.join(_REPO_ROOT, rel_path), encoding="utf-8") as handle:
        return handle.read()


def _expand_includes(text, depth=0):
    """Expand `{% include "..." %}` the way Jinja does: verbatim, in place.

    Verbatim is the whole point — the bug exists precisely because the include's
    own line breaks land inside the surrounding JS token.
    """
    if depth > 5:
        return text

    def repl(match):
        target = match.group(1)
        # Template paths are rooted at the app dir that owns them; every include
        # used by the login page lives under frappe/.
        return _expand_includes(_read(os.path.join("frappe", target)), depth + 1)

    return _INCLUDE_RE.sub(repl, text)


def _render_login_scripts():
    """Return the login page's inline JS with includes expanded, as served."""
    source = _JINJA_COMMENT_RE.sub("", _expand_includes(_read(LOGIN_TEMPLATE)))
    scripts = _SCRIPT_RE.findall(source)
    # Jinja expressions become values at render time; collapse them to a single
    # safe token so the scanner sees the JS shape rather than the template.
    scripts = [_JINJA_EXPR_RE.sub("0", s) for s in scripts]
    # Control-flow tags emit nothing.
    scripts = [_JINJA_TAG_RE.sub("", s) for s in scripts]
    return scripts


def find_unterminated_string_literals(js):
    """Return [(line_number, quote_char, line_text)] for literals left open.

    A single- or double-quoted JavaScript string may not span a raw newline. Any
    that does is a parse-time SyntaxError that takes the whole <script> with it.

    The scan tracks block comments and template literals (backticks legally DO
    span newlines, which is the fix) and strips `//` comments outside strings.
    It is intentionally narrow: it detects exactly this defect class rather than
    attempting to be a JavaScript parser.
    """
    offenders = []
    in_block_comment = False
    in_template = False

    for lineno, line in enumerate(js.split("\n"), start=1):
        i = 0
        quote = None
        while i < len(line):
            ch = line[i]

            if in_block_comment:
                if line.startswith("*/", i):
                    in_block_comment = False
                    i += 2
                    continue
                i += 1
                continue

            if in_template:
                if ch == "\\":
                    i += 2
                    continue
                if ch == "`":
                    in_template = False
                i += 1
                continue

            if quote:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    quote = None
                i += 1
                continue

            # Outside any string/comment.
            if line.startswith("//", i):
                break
            if line.startswith("/*", i):
                in_block_comment = True
                i += 2
                continue
            if ch == "`":
                in_template = True
                i += 1
                continue
            if ch in ("'", '"'):
                quote = ch
                i += 1
                continue
            i += 1

        if quote:
            offenders.append((lineno, quote, line.strip()))

    return offenders


class TestLoginPageScriptParses(unittest.TestCase):
    """The login page's inline JavaScript must survive Jinja expansion."""

    def testNoStringLiteralSpansANewlineFail(self):
        for index, script in enumerate(_render_login_scripts()):
            offenders = find_unterminated_string_literals(script)
            self.assertEqual(
                offenders,
                [],
                f"login.html inline script #{index} leaves a string literal open "
                "at a line break, which is a parse-time SyntaxError that kills "
                "the WHOLE script block — including the SSO link wiring (bug "
                "e1a9e4e9). Use a template literal (backticks) for multi-line "
                f"content. Offenders: {offenders!r}",
            )

    def testSplashIncludeIsNotInsideAQuotedStringFail(self):
        """The specific call site, checked before expansion, for a clear message."""
        source = _read(LOGIN_TEMPLATE)
        bad = re.findall(
            r"['\"]\s*\{%-?\s*include\s+\"[^\"]*splash_screen\.html\"",
            source,
        )
        self.assertEqual(
            bad,
            [],
            "splash_screen.html is MULTI-LINE and must never be embedded in a "
            "single- or double-quoted JS string in login.html — that is exactly "
            "bug e1a9e4e9. Use backticks, as templates/includes/login/login.js "
            "does.",
        )

    def testSplashIncludeIsStillMultiLine(self):
        """Documents WHY the quoting matters, so the fix is not 'simplified' away.

        If someone ever collapses the include back to one line, single quotes
        would start working again by accident — and the next person to
        pretty-print it would silently reintroduce the outage. Asserting the
        include is multi-line keeps the backticks load-bearing and honest.
        """
        splash = _read(SPLASH_INCLUDE).strip()
        self.assertIn(
            "\n",
            splash,
            "splash_screen.html is now single-line; if that is intentional, the "
            "reasoning in login.html about template literals needs updating too",
        )


class TestSsoControlHasServerRenderedHref(unittest.TestCase):
    """"Sign in via Exe SSO" must not depend on the inline script to work."""

    def _sso_anchor(self):
        source = _read(LOGIN_TEMPLATE)
        match = re.search(r"<a\b[^>]*id=\"exe-sso-link\"[^>]*>", source, re.DOTALL)
        self.assertIsNotNone(match, "login.html must carry the #exe-sso-link control")
        return match.group(0)

    def testAnchorCarriesLiteralHrefInMarkup(self):
        anchor = self._sso_anchor()
        href = re.search(r"href=\"([^\"]+)\"", anchor)
        self.assertIsNotNone(
            href,
            "#exe-sso-link has NO href in the server-rendered markup. It was "
            "wired only by the inline script, so when that script failed to "
            "parse the button became a no-op and SSO into ERP was dead (bug "
            "e1a9e4e9). Put the href in the markup.",
        )
        self.assertEqual(
            href.group(1),
            SSO_START_PATH,
            "#exe-sso-link must point at gotrue_login_start — never at "
            "gotrue_login_callback, which would bypass the exe_sso_state CSRF "
            "cookie initializer and fail the callback's state check.",
        )

    def testAnchorHrefMatchesTheScriptAssignment(self):
        """Markup and script must not drift into disagreeing about the target."""
        source = _read(LOGIN_TEMPLATE)
        assigned = re.findall(r"ssoLink\.href\s*=\s*'([^']+)'", source)
        for target in assigned:
            self.assertEqual(
                target,
                SSO_START_PATH,
                "the script assigns #exe-sso-link a different target than the "
                "markup href; they must stay in sync",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
