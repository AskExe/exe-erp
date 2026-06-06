"""
Exe Bridge — Cross-database event bridge for exe-os stack integration.

Emits structured events from exe-erp (Frappe ORM, exe_erp database) to
the raw.raw_events landing pad in exedb (shared PostgreSQL instance).

Architecture:
  exe-erp doc_events hooks → emit_raw_event() → psycopg2 → exedb.raw.raw_events

This enables:
  - Cross-service data flow visibility
  - Projection workers to sync ERP data to crm/wiki/gateway
  - exe-monitor telemetry aggregation
  - Company Brain queries across the full stack
"""
