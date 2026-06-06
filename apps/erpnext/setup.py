# Shim for frappe-bench compatibility.
# This file is copied to /opt/erpnext-app/setup.py in the Docker build.
# /opt/erpnext-app/erpnext/ is a symlink to the actual erpnext source.
# Standard bench layout: apps/erpnext/setup.py + apps/erpnext/erpnext/
from setuptools import setup, find_packages

setup(
    name="erpnext",
    version="17.0.0.dev0",
    packages=find_packages(),
)
