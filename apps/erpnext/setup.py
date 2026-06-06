# Shim for frappe-bench compatibility.
# bench reads this file to discover the app name.
# Actual build config is in pyproject.toml.
from setuptools import setup

setup(
    name="erpnext",
    version="17.0.0-dev",
)
