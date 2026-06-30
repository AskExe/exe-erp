"""
Exe Bridge — Database connection to exedb (raw.raw_events landing pad).

Uses a SEPARATE psycopg2 connection to the shared exedb instance,
independent of Frappe's ORM which connects to exe_erp.

Connection is lazy-initialized and pooled per worker process.
Falls back gracefully if exedb is unavailable — never blocks ERP operations.
"""

import logging
import os
import threading

logger = logging.getLogger("exe_bridge")

# Thread-local connection pool (one connection per worker/thread)
_local = threading.local()

# Emit the "bridge unconfigured" warning at most once per process to keep the
# failure visible without spamming logs on every event (bug 12f4c334).
_warned_unconfigured = False


def get_connection():
	"""Get or create a psycopg2 connection to exedb.

	Returns None if connection fails — caller must handle gracefully.
	Connection is cached per-thread for the process lifetime.
	"""
	conn = getattr(_local, "bridge_conn", None)

	# Check if existing connection is still alive
	if conn is not None:
		try:
			conn.isolation_level  # Triggers check
			return conn
		except Exception:
			# Connection is dead, recreate
			try:
				conn.close()
			except Exception:
				pass
			_local.bridge_conn = None

	# Require an explicit, fully-specified DSN. The previous implicit fallback
	# (host=exe-db, dbname=exedb, user=exe, password=$DB_PASSWORD) guessed
	# credentials that almost never matched the exedb bridge user, so raw event
	# emission failed SILENTLY (bug 12f4c334). Fail closed + visible instead:
	# if EXE_BRIDGE_DATABASE_URL is unset, the bridge is explicitly disabled and
	# we log a single clear warning rather than attempting a wrong-credential
	# connection on every event.
	global _warned_unconfigured
	dsn = os.environ.get("EXE_BRIDGE_DATABASE_URL")
	if not dsn:
		if not _warned_unconfigured:
			logger.warning(
				"exe_bridge: EXE_BRIDGE_DATABASE_URL is not set — raw.raw_events "
				"emission is DISABLED. Set EXE_BRIDGE_DATABASE_URL to the exedb "
				"bridge DSN to enable trace/event forwarding."
			)
			_warned_unconfigured = True
		return None

	# Create new connection
	try:
		import psycopg2

		conn = psycopg2.connect(dsn, connect_timeout=5)
		conn.autocommit = True  # Each event is an independent write
		_local.bridge_conn = conn
		logger.info("exe_bridge: Connected to exedb for event emission")
		return conn
	except ImportError:
		logger.warning("exe_bridge: psycopg2 not installed — bridge disabled")
		return None
	except Exception as e:
		logger.warning(f"exe_bridge: Cannot connect to exedb — {e}")
		return None


def close_connection():
	"""Close the thread-local bridge connection (called on worker shutdown)."""
	conn = getattr(_local, "bridge_conn", None)
	if conn is not None:
		try:
			conn.close()
		except Exception:
			pass
		_local.bridge_conn = None
