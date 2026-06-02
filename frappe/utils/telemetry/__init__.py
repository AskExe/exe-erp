"""Telemetry DISABLED — Exe ERP hard fork. Zero phone-home."""


def add_bootinfo(bootinfo):
    bootinfo.enable_telemetry = False
    bootinfo.telemetry_provider = []


def capture(event, app, **kwargs):
    pass


def site_age():
    return 0


# backward compat stubs
def init_telemetry(*a, **kw):
    pass

def capture_doc(*a, **kw):
    pass

POSTHOG_HOST_FIELD = ""
POSTHOG_PROJECT_FIELD = ""
