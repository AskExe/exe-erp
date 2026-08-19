# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: MIT. See LICENSE

import base64
import json
from unittest.mock import MagicMock, patch

from frappe.tests import UnitTestCase
from frappe.utils.oauth import login_oauth_user


def _encode_state(payload: dict) -> str:
	return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


class TestOAuthState(UnitTestCase):
	def test_sso_completes_without_state_echo(self):
		"""SSO must complete even when the auth domain does not echo state back."""
		with (
			patch("frappe.respond_as_web_page") as respond_as_web_page,
			patch("frappe.utils.oauth.get_email", return_value="sso@example.com"),
			patch("frappe.utils.oauth.update_oauth_user", return_value=True),
			patch("frappe.local", new_callable=MagicMock) as local,
			patch("frappe.db", new_callable=MagicMock),
			patch("frappe.utils.oauth.redirect_post_login") as redirect_post_login,
			patch("frappe.utils.cint", return_value=0),
		):
			login_oauth_user({"email": "sso@example.com"}, provider="frappe", state=None)

			respond_as_web_page.assert_not_called()
			local.login_manager.login_as.assert_called_once_with("sso@example.com")
			redirect_post_login.assert_called_once()

	def test_sso_completes_with_empty_state(self):
		"""An empty-string state is equivalent to no state echo: SSO must complete."""
		with (
			patch("frappe.respond_as_web_page") as respond_as_web_page,
			patch("frappe.utils.oauth.get_email", return_value="sso@example.com"),
			patch("frappe.utils.oauth.update_oauth_user", return_value=True),
			patch("frappe.local", new_callable=MagicMock) as local,
			patch("frappe.db", new_callable=MagicMock),
			patch("frappe.utils.oauth.redirect_post_login") as redirect_post_login,
			patch("frappe.utils.cint", return_value=0),
		):
			login_oauth_user({"email": "sso@example.com"}, provider="frappe", state="")

			respond_as_web_page.assert_not_called()
			local.login_manager.login_as.assert_called_once_with("sso@example.com")
			redirect_post_login.assert_called_once()

	def test_csrf_enforced_when_state_present_without_token_Fail(self):
		"""A echoed state without the CSRF token must be rejected with 417."""
		state = _encode_state({"redirect_to": "/app"})
		with (
			patch("frappe.respond_as_web_page") as respond_as_web_page,
			patch("frappe.utils.oauth.get_email", return_value="sso@example.com"),
			patch("frappe.utils.oauth.update_oauth_user", return_value=True),
			patch("frappe.local", new_callable=MagicMock) as local,
			patch("frappe.db", new_callable=MagicMock),
			patch("frappe.utils.oauth.redirect_post_login"),
			patch("frappe.utils.cint", return_value=0),
		):
			login_oauth_user({"email": "sso@example.com"}, provider="frappe", state=state)

			respond_as_web_page.assert_called_once()
			self.assertEqual(respond_as_web_page.call_args.kwargs.get("http_status_code"), 417)
			local.login_manager.login_as.assert_not_called()

	def test_sso_completes_with_valid_state(self):
		"""A valid echoed state carrying the CSRF token completes SSO."""
		state = _encode_state({"token": "abc123", "redirect_to": "/app"})
		with (
			patch("frappe.respond_as_web_page") as respond_as_web_page,
			patch("frappe.utils.oauth.get_email", return_value="sso@example.com"),
			patch("frappe.utils.oauth.update_oauth_user", return_value=True),
			patch("frappe.local", new_callable=MagicMock) as local,
			patch("frappe.db", new_callable=MagicMock),
			patch("frappe.utils.oauth.redirect_post_login"),
			patch("frappe.utils.cint", return_value=0),
		):
			login_oauth_user({"email": "sso@example.com"}, provider="frappe", state=state)

			local.login_manager.login_as.assert_called_once_with("sso@example.com")
			respond_as_web_page.assert_not_called()

	def test_malformed_state_rejected_Fail(self):
		"""A state that cannot be decoded is treated as tampered and rejected."""
		with (
			patch("frappe.respond_as_web_page") as respond_as_web_page,
			patch("frappe.utils.oauth.get_email", return_value="sso@example.com"),
			patch("frappe.utils.oauth.update_oauth_user", return_value=True),
			patch("frappe.local", new_callable=MagicMock) as local,
			patch("frappe.db", new_callable=MagicMock),
			patch("frappe.utils.oauth.redirect_post_login"),
			patch("frappe.utils.cint", return_value=0),
		):
			login_oauth_user({"email": "sso@example.com"}, provider="frappe", state="!!!not-base64!!!")

			respond_as_web_page.assert_called_once()
			self.assertEqual(respond_as_web_page.call_args.kwargs.get("http_status_code"), 417)
			local.login_manager.login_as.assert_not_called()
