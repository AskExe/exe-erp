"""
/login SSO auto-redirect contract (bug 3cb4871c) + loop regression (bug 62c42448).

THE DEFECT
──────────
erp.<apex> was the only product in the stack that did NOT hand an
unauthenticated visitor to auth.<apex>. crm and wiki both auto-redirect; ERP
parked the visitor on its own local Frappe login form.

The cause was the loop fix for bug 62c42448. `frappe/templates/base.html`'s
Guest guard was given a blanket exemption:

    {% if frappe.session.user == 'Guest' and path != 'login' %}

That stopped the /login -> SSO -> /login spin, but a blanket exemption cannot
tell a Guest's FIRST arrival at /login (never a loop, must redirect) from a
re-entry during a handoff already in flight (must not redirect). It suppressed
both, so the feature was gone.

THE FIX UNDER TEST
──────────────────
The /login decision moved SERVER-side into `frappe/www/login.py`, gated on the
HttpOnly `exe_sso_state` cookie that `gotrue_login_start` sets and
`gotrue_login_callback` deletes — a precise "a handoff is already in flight"
signal that a browser-side script cannot even read. base.html keeps its
`path != 'login'` clause so the two mechanisms never re-decide each other's
page.

WHAT THIS FILE GUARDS
─────────────────────
1. TestSsoAutoredirectDecisionFail — the pure policy in exe_perms.
2. TestLoginPageRedirectsGuestToSsoFail — drives the REAL `get_context()` from
   `frappe/www/login.py` against a stubbed frappe, and asserts a fresh
   unauthenticated GET /login raises `frappe.Redirect` to gotrue_login_start.
   This is the test that goes red on the pre-fix parent commit.
3. TestLoginPageDoesNotLoopFail — the same driver with an `exe_sso_state`
   cookie present (the post-SSO bounce-back) must NOT redirect. Regression
   coverage for bug 62c42448.
4. TestBaseTemplateGiveUpFail — base.html's give-up branch must target the
   explicit `?sso=0` local form and must not clear its one-shot key.

Deliberately frappe-free, like its siblings, so it runs under plain
`python -m unittest` in CI with no bench and no live site. Registered in
.github/scripts/ci_python_tests.py — a module not listed there is a module CI
does not run.
"""

import importlib.util
import os
import re
import sys
import types
import unittest
from unittest import mock

# .../apps/erpnext/erpnext/exe_auth/ -> up 4
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

LOGIN_PY = os.path.join(_REPO_ROOT, "frappe", "www", "login.py")
BASE_HTML = os.path.join(_REPO_ROOT, "frappe", "templates", "base.html")
EXE_PERMS_PY = os.path.join(_REPO_ROOT, "apps", "erpnext", "erpnext", "exe_auth", "exe_perms.py")

SSO_START_PATH = "/api/method/erpnext.exe_auth.api.gotrue_login_start"
STATE_COOKIE = "exe_sso_state"


def _load_by_path(name, path):
    """Load a module from its file path, bypassing package __init__ imports."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


exe_perms = _load_by_path("exe_perms_under_test", EXE_PERMS_PY)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Pure policy
# ─────────────────────────────────────────────────────────────────────────────


class TestSsoAutoredirectDecisionFail(unittest.TestCase):
    def decide(self, session_user="Guest", cookies=None, query_args=None, gotrue_configured=True):
        return exe_perms.sso_autoredirect_decision(
            session_user,
            {} if cookies is None else cookies,
            {} if query_args is None else query_args,
            gotrue_configured,
        )

    def testFreshGuestVisitRedirects(self):
        """No handoff in flight -> hand off. This is the whole bug."""
        self.assertTrue(self.decide())

    def testHandoffAlreadyInFlightDoesNotRedirectFail(self):
        """bug 62c42448: a bounce-back carries the state cookie -> stand down."""
        self.assertFalse(self.decide(cookies={STATE_COOKIE: "nonce-abc"}))

    def testAuthenticatedUserDoesNotRedirectFail(self):
        self.assertFalse(self.decide(session_user="admin@example.com"))

    def testSsoNotConfiguredDoesNotRedirectFail(self):
        """A password-only deployment must never be sent to a handoff that
        cannot complete — that would strand every visitor away from the only
        login form the site has."""
        self.assertFalse(self.decide(gotrue_configured=False))

    def testExplicitOptOutDoesNotRedirectFail(self):
        for value in ("0", "off", "false", "no", "local", "OFF"):
            with self.subTest(value=value):
                self.assertFalse(self.decide(query_args={"sso": value}))

    def testUnrelatedQueryParamsStillRedirect(self):
        self.assertTrue(self.decide(query_args={"redirect-to": "/app/item", "sso": "1"}))

    def testBlankStateCookieStillRedirects(self):
        """gotrue_login_start always writes a 32-byte nonce. Treating a BLANK
        cookie as in-flight would let anything able to plant an empty
        `exe_sso_state` permanently disable SSO on the deployment."""
        for value in ("", "   "):
            with self.subTest(value=value):
                self.assertTrue(self.decide(cookies={STATE_COOKIE: value}))

    def testUnreadableInputsFailSafeToLocalForm(self):
        """Fail-safe direction: unknown input -> render the local form. A false
        negative costs one click; a false positive costs a loop or a lockout."""

        class Exploding:
            def get(self, *_args, **_kwargs):
                raise RuntimeError("cookie jar exploded")

        self.assertFalse(self.decide(cookies=Exploding()))
        self.assertFalse(self.decide(query_args=Exploding()))


# ─────────────────────────────────────────────────────────────────────────────
# 2 + 3. The real login.py get_context(), driven against a stubbed frappe
# ─────────────────────────────────────────────────────────────────────────────


class _Redirect(Exception):
    """Stand-in for frappe.Redirect."""


class _LoginPageDriver:
    """Import and run `frappe/www/login.py:get_context` with no bench.

    login.py imports ~a dozen frappe submodules at module scope; each is stubbed
    so `from frappe.x import y` resolves. `frappe` itself gets real behaviour for
    exactly the state the decision reads (session, request cookies/args, conf,
    local.flags) so the assertion is about the code under test, not the stubs.

    `erpnext.exe_auth.exe_perms` is stubbed with the REAL module loaded from
    disk — the policy is genuinely executed, not mocked away.
    """

    _FRAPPE_SUBMODULES = (
        "frappe.utils",
        "frappe.utils.data",
        "frappe.utils.html_utils",
        "frappe.utils.jinja",
        "frappe.utils.oauth",
        "frappe.utils.password",
        "frappe.apps",
        "frappe.auth",
        "frappe.rate_limiter",
        "frappe.core",
        "frappe.core.doctype",
        "frappe.core.doctype.navbar_settings",
        "frappe.core.doctype.navbar_settings.navbar_settings",
        "frappe.website",
        "frappe.website.utils",
        "frappe.integrations",
    )

    def __init__(
        self,
        session_user="Guest",
        cookies=None,
        query_args=None,
        gotrue_url="http://gotrue:9999",
    ):
        self.frappe = mock.MagicMock(name="frappe")
        self.frappe.Redirect = _Redirect
        self.frappe.session = mock.MagicMock()
        self.frappe.session.user = session_user
        self.frappe.local = mock.MagicMock()
        self.frappe.local.request.cookies = dict(cookies or {})
        self.frappe.local.request.args = dict(query_args or {})
        self.frappe.local.flags = types.SimpleNamespace(redirect_location=None)
        self.frappe.conf = {"gotrue_url": gotrue_url} if gotrue_url else {}

        erpnext_pkg = types.ModuleType("erpnext")
        exe_auth_pkg = types.ModuleType("erpnext.exe_auth")
        exe_auth_pkg.exe_perms = exe_perms
        erpnext_pkg.exe_auth = exe_auth_pkg

        self._modules = {
            "frappe": self.frappe,
            "erpnext": erpnext_pkg,
            "erpnext.exe_auth": exe_auth_pkg,
        }
        for name in self._FRAPPE_SUBMODULES:
            self._modules[name] = mock.MagicMock(name=name)

    def run(self):
        """Return the redirect location get_context chose, or None if it chose
        to render the local login form instead."""
        with mock.patch.dict(sys.modules, self._modules):
            login = _load_by_path("exe_login_under_test", LOGIN_PY)
            try:
                login.get_context(mock.MagicMock())
            except _Redirect:
                return self.frappe.local.flags.redirect_location
            except Exception:
                # Everything AFTER the decision point renders the page against
                # MagicMocks and is free to blow up — that is not this test's
                # subject. Reaching here proves no Redirect was raised, which is
                # exactly what the no-loop assertions are about.
                pass
            return None


class TestLoginPageRedirectsGuestToSsoFail(unittest.TestCase):
    """(a) A FRESH unauthenticated visit to /login hands off to SSO.

    RED on the pre-fix parent commit: there, /login rendered the local form and
    no Redirect was ever raised.
    """

    def testFreshGuestVisitRedirectsToSsoStartFail(self):
        location = _LoginPageDriver().run()
        self.assertEqual(
            location,
            SSO_START_PATH,
            "a fresh unauthenticated GET /login must 302 into the server-side "
            "SSO entry point, the way crm.<apex> and wiki.<apex> do (bug "
            "3cb4871c). Never straight to gotrue_login_callback: only "
            "gotrue_login_start mints the exe_sso_state CSRF cookie.",
        )

    def testSsoNotConfiguredRendersLocalFormFail(self):
        self.assertIsNone(_LoginPageDriver(gotrue_url=None).run())

    def testExplicitOptOutRendersLocalFormFail(self):
        self.assertIsNone(_LoginPageDriver(query_args={"sso": "0"}).run())


class TestLoginPageDoesNotLoopFail(unittest.TestCase):
    """(b) A simulated post-SSO bounce-back to /login must NOT redirect again.

    Regression coverage for bug 62c42448 — the infinite /login -> SSO -> /login
    spin. The browser arrives still carrying the `exe_sso_state` cookie that
    gotrue_login_start set, which is what proves the handoff is already in
    flight rather than being asked for afresh.
    """

    def testBounceBackWithStateCookieDoesNotRedirectFail(self):
        location = _LoginPageDriver(cookies={STATE_COOKIE: "nonce-from-login-start"}).run()
        self.assertIsNone(
            location,
            "a Guest bounced BACK to /login mid-handoff must be shown the local "
            "form, not redirected into SSO a second time (bug 62c42448)",
        )

    def testRepeatedBouncesNeverRedirectFail(self):
        """The brake must not be one-shot-and-then-off: as long as the attempt
        is in flight, every re-entry stands down."""
        for attempt in range(5):
            with self.subTest(attempt=attempt):
                self.assertIsNone(_LoginPageDriver(cookies={STATE_COOKIE: "nonce"}).run())


# ─────────────────────────────────────────────────────────────────────────────
# 4. base.html give-up branch
# ─────────────────────────────────────────────────────────────────────────────


class TestBaseTemplateGiveUpFail(unittest.TestCase):
    def _guard_body(self):
        with open(BASE_HTML, encoding="utf-8") as handle:
            content = handle.read()
        guard = re.search(
            r"\{% if frappe\.session\.user == 'Guest'.*?\{% endif %\}", content, re.DOTALL
        )
        self.assertIsNotNone(guard, "base.html must still carry a Guest SSO guard")
        # Strip `//` comments: this guard deliberately quotes the old broken
        # shapes in its own explanation, and a check a comment can trip is a
        # check that teaches people to stop explaining themselves.
        return re.sub(r"//[^\n]*", "", guard.group(0))

    def testGiveUpTargetsExplicitLocalFormFail(self):
        """A bare `/login` give-up would be bounced straight back out by the new
        server-side redirect whenever the callback had already eaten the state
        cookie — the give-up would become the next hop of the loop."""
        body = self._guard_body()
        self.assertIn("/login?sso=0", body)
        self.assertNotRegex(
            body,
            r"location\.href\s*=\s*'/login'",
            "the give-up branch must ask for the local form EXPLICITLY "
            "(`/login?sso=0`), never a bare `/login`",
        )

    def testGiveUpDoesNotClearOneShotKeyFail(self):
        """Clearing the per-tab key re-armed the guard on the very next Guest
        page. A handoff that completes but whose Frappe session does not stick
        (blocked cookies, clock skew, host mismatch) then span
        /desk -> /login -> SSO -> /desk forever."""
        body = self._guard_body()
        self.assertNotIn(
            "sessionStorage.removeItem",
            body,
            "the Guest guard must not clear its one-shot key — that re-arms the "
            "handoff on the next Guest page and reopens the spin",
        )

    def testLoginPathStaysDelegatedToServerSideFail(self):
        """base.html must keep standing down on /login. Restoring the head
        script there would put two mechanisms in charge of the same page, each
        re-deciding the other's outcome — which is what spun in bug 62c42448."""
        with open(BASE_HTML, encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("{% if frappe.session.user == 'Guest' and path != 'login' %}", content)


if __name__ == "__main__":
    unittest.main()
