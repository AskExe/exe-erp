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

	# Create new connection
	try:
		import psycopg2

		# Use keyword args instead of DSN to avoid password in a single string
		dsn = os.environ.get("EXE_BRIDGE_DATABASE_URL")
		if dsn:
			conn = psycopg2.connect(dsn, connect_timeout=5)
		else:
			host = os.environ.get("DB_HOST", "exe-db")
			port = os.environ.get("DB_PORT", "5432")
			password = os.environ.get("DB_PASSWORD", "")
			conn = psycopg2.connect(
				host=host, port=port, dbname="exedb",
				user="exe", password=password,
				connect_timeout=5,
			)
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
