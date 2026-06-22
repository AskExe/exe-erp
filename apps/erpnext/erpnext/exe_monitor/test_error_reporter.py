"""Unit tests for exe_monitor.error_reporter header behavior.

Verifies the error reporter sends the monitor key as the `X-Monitor-Key`
header (matching exe-monitor's contract) and NOT as `Authorization: Bearer`.

Runnable standalone (no bench/site required): report_error imports `requests`
lazily and only touches frappe inside on_request_error, so the module can be
loaded directly by file path without importing the frappe-dependent erpnext
package. Run with:
    python -m unittest erpnext.exe_monitor.test_error_reporter   (inside bench)
or standalone:
    python apps/erpnext/erpnext/exe_monitor/test_error_reporter.py
"""

import importlib.util
import os
import unittest
from unittest import mock

_MODULE_PATH = os.path.join(os.path.dirname(__file__), "error_reporter.py")


def _load_module(env):
	"""Load error_reporter.py by file path with the given environment so the
	module-level MONITOR_KEY is computed against `env`. Avoids importing the
	frappe-dependent `erpnext` package."""
	with mock.patch.dict(os.environ, env, clear=True):
		spec = importlib.util.spec_from_file_location(
			"exe_monitor_error_reporter_under_test", _MODULE_PATH
		)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
	return module


class _Resp:
	status_code = 200


class TestErrorReporterHeaders(unittest.TestCase):
	def test_sends_x_monitor_key_header(self):
		module = _load_module(
			{"MONITOR_API_KEY": "test-monitor-key-123", "ERROR_REPORTING_ENABLED": "true"}
		)
		with mock.patch("requests.post", return_value=_Resp()) as post:
			module.report_error("boom", context={"endpoint": "/x"}, severity="error")

		self.assertTrue(post.called)
		headers = post.call_args.kwargs["headers"]
		self.assertEqual(headers["X-Monitor-Key"], "test-monitor-key-123")
		self.assertNotIn("Authorization", headers)

	def test_exe_monitor_key_fallback(self):
		module = _load_module(
			{"EXE_MONITOR_KEY": "fallback-key-456", "ERROR_REPORTING_ENABLED": "true"}
		)
		with mock.patch("requests.post", return_value=_Resp()) as post:
			module.report_error("boom", severity="error")

		self.assertTrue(post.called)
		headers = post.call_args.kwargs["headers"]
		self.assertEqual(headers["X-Monitor-Key"], "fallback-key-456")
		self.assertNotIn("Authorization", headers)

	def test_no_header_when_key_unset(self):
		module = _load_module({"ERROR_REPORTING_ENABLED": "true"})
		with mock.patch("requests.post", return_value=_Resp()) as post:
			module.report_error("boom", severity="error")

		self.assertTrue(post.called)
		headers = post.call_args.kwargs["headers"]
		self.assertNotIn("X-Monitor-Key", headers)
		self.assertNotIn("Authorization", headers)


if __name__ == "__main__":
	unittest.main()
