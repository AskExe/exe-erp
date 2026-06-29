"""
Exe Bridge — Doc event handlers that emit structured events to raw.raw_events.

Wired via hooks.py doc_events:
  "*": {
      "after_insert": "erpnext.exe_bridge.events.on_after_insert",
      "on_update": "erpnext.exe_bridge.events.on_update",
      "on_submit": "erpnext.exe_bridge.events.on_submit",
      "on_cancel": "erpnext.exe_bridge.events.on_cancel",
      "on_trash": "erpnext.exe_bridge.events.on_trash",
  }

Design principles:
  - NEVER block or slow down ERP operations — all writes are fire-and-forget
  - NEVER raise exceptions from bridge code — swallow and log
  - Emit ONLY for business-relevant doctypes (configurable allowlist)
  - Payload includes the full doc as-dict for maximum projection flexibility
"""

import json
import logging
import uuid
from datetime import datetime, timezone

import frappe

from erpnext.exe_bridge.connection import get_connection

logger = logging.getLogger("exe_bridge")

# ── Allowlist: which doctypes emit events ────────────────────────
# Only business-relevant doctypes. System/metadata doctypes are excluded
# to avoid noise. Add new doctypes here as integration needs grow.
BRIDGE_DOCTYPES = {
	# Sales cycle
	"Customer",
	"Quotation",
	"Sales Order",
	"Sales Invoice",
	"Delivery Note",
	"Payment Entry",
	# Purchase cycle
	"Supplier",
	"Purchase Order",
	"Purchase Invoice",
	"Purchase Receipt",
	# Inventory
	"Item",
	"Stock Entry",
	"Stock Reconciliation",
	"Material Request",
	# Manufacturing
	"BOM",
	"Work Order",
	# Projects
	"Project",
	"Task",
	"Timesheet",
	# CRM
	"Lead",
	"Opportunity",
	"Contact",
	"Address",
	# HR
	"Employee",
	# Accounting
	"Journal Entry",
	# Assets
	"Asset",
	# Support
	"Issue",
	# Quality
	"Quality Inspection",
}

# Fields to EXCLUDE from payload (security/noise)
EXCLUDED_FIELDS = {
	"password",
	"_password",
	"__Auth",
	"session_data",
	"_comments",
	"_assign",
	"_liked_by",
	"_seen",
}

# Maximum payload size (bytes) — truncate if exceeded
MAX_PAYLOAD_BYTES = 64 * 1024  # 64 KB


def _should_emit(doc):
	"""Check if this doc event should be emitted to the bridge."""
	if not doc or not doc.doctype:
		return False

	# Only emit for allowlisted doctypes
	if doc.doctype not in BRIDGE_DOCTYPES:
		return False

	# Skip if bridge is explicitly disabled
	if frappe.conf.get("exe_bridge_disabled"):
		return False

	return True


def _build_payload(doc):
	"""Build a clean, serializable payload from the Frappe doc."""
	try:
		data = doc.as_dict(no_default_fields=False)

		# Remove excluded fields
		for field in EXCLUDED_FIELDS:
			data.pop(field, None)

		# Remove child table internal fields
		for _key, value in list(data.items()):
			if isinstance(value, list):
				for row in value:
					if isinstance(row, dict):
						for f in EXCLUDED_FIELDS:
							row.pop(f, None)

		# Serialize to JSON, respecting size limit
		payload_str = json.dumps(data, default=str, ensure_ascii=False)
		if len(payload_str.encode("utf-8")) > MAX_PAYLOAD_BYTES:
			# Truncate: keep top-level fields only, drop child tables
			slim = {
				k: v
				for k, v in data.items()
				if not isinstance(v, list)
			}
			slim["_truncated"] = True
			slim["_original_size"] = len(payload_str)
			payload_str = json.dumps(slim, default=str, ensure_ascii=False)

		return json.loads(payload_str)
	except Exception as e:
		logger.warning(f"exe_bridge: payload build failed for {doc.doctype}/{doc.name}: {e}")
		return {"doctype": doc.doctype, "name": doc.name, "_error": str(e)}


def _build_metadata(doc, event_type):
	"""Build metadata for the raw event."""
	return {
		"site": frappe.local.site or "unknown",
		"user": frappe.session.user if frappe.session else "System",
		"event_trigger": event_type,
		"modified": str(doc.modified) if hasattr(doc, "modified") else None,
		"docstatus": getattr(doc, "docstatus", 0),
		"workflow_state": getattr(doc, "workflow_state", None),
	}


def _emit_event(doc, event_type):
	"""Core event emission — write to exedb.raw.raw_events.

	Fire-and-forget: never raises, never blocks.
	"""
	if not _should_emit(doc):
		return

	conn = get_connection()
	if conn is None:
		return  # Bridge unavailable — silently skip

	try:
		event_id = str(uuid.uuid4())
		source_id = str(doc.name)
		doctype_slug = doc.doctype.lower().replace(" ", "_")
		full_event_type = f"{doctype_slug}.{event_type}"
		payload = _build_payload(doc)
		metadata = _build_metadata(doc, event_type)
		now = datetime.now(timezone.utc)

		cursor = conn.cursor()
		cursor.execute(
			"""
			INSERT INTO raw.raw_events (id, source, source_id, event_type, payload, metadata, timestamp)
			VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
			ON CONFLICT (source, source_id, event_type)
			WHERE source_id IS NOT NULL
			DO UPDATE SET
				payload = EXCLUDED.payload,
				metadata = EXCLUDED.metadata,
				timestamp = EXCLUDED.timestamp,
				processed_at = NULL
			""",
			(
				event_id,
				"erp",
				source_id,
				full_event_type,
				json.dumps(payload, default=str),
				json.dumps(metadata, default=str),
				now,
			),
		)
		cursor.close()

	except Exception as e:
		logger.warning(f"exe_bridge: event emission failed for {doc.doctype}/{doc.name}: {e}")
		# Reset connection on failure — it may be stale
		try:
			from erpnext.exe_bridge.connection import close_connection
			close_connection()
		except Exception:
			pass


# ── Frappe doc_event handlers ────────────────────────────────────
# These are wired in hooks.py and called by Frappe's event system

def on_after_insert(doc, method=None):
	"""Fired after a new document is saved for the first time."""
	_emit_event(doc, "created")


def on_update(doc, method=None):
	"""Fired after an existing document is updated."""
	_emit_event(doc, "updated")


def on_submit(doc, method=None):
	"""Fired when a submittable document is submitted (docstatus=1)."""
	_emit_event(doc, "submitted")


def on_cancel(doc, method=None):
	"""Fired when a submitted document is cancelled (docstatus=2)."""
	_emit_event(doc, "cancelled")


def on_trash(doc, method=None):
	"""Fired when a document is permanently deleted."""
	_emit_event(doc, "deleted")
