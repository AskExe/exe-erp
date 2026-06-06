# Shim for frappe-bench compatibility.
# This file is copied to /opt/frappe-app/setup.py in the Docker build.
# /opt/frappe-app/frappe/ is a symlink to the actual frappe source.
# Standard bench layout: apps/frappe/setup.py + apps/frappe/frappe/
from setuptools import setup, find_packages

setup(
    name="frappe",
    version="17.0.0.dev0",
    packages=find_packages(),
)
