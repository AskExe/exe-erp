"""
Exe Monitor — Error forwarding and health reporting for exe-monitor-hub.

Matches the pattern used by exe-crm (error forwarding to MONITOR_ERROR_URL)
and exe-gateway (alerts.ts → monitor-hub).

Components:
  - error_reporter: Forward 5xx errors and critical failures
  - health: Enhanced health endpoint with component status
"""
