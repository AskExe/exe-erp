# Shim for frappe-bench compatibility.
# When bench clones frappe into apps/frappe/, the directory structure is:
#   apps/frappe/
#     setup.py        ← this file
#     __init__.py      ← frappe package root
#     core/
#     desk/
#     api/
#     ...
# setuptools auto-discovery gets confused by the flat layout.
# We explicitly declare the package structure.
from setuptools import setup, find_packages

setup(
    name="frappe",
    version="17.0.0.dev0",
    # Current directory IS the frappe package (contains __init__.py).
    # Tell setuptools to treat "." as package "frappe" and find subpackages.
    package_dir={"frappe": "."},
    packages=["frappe"] + ["frappe." + p for p in find_packages(where=".")],
)
