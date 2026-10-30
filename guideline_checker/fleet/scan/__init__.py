"""fleet-scan — static, read-only conformance scanner over the GitHub REST API.

Kept behind the ``scan`` extra (pydantic + httpx). Nothing is imported here so that
``guideline_checker.cli`` can probe the package without pulling the optional deps.
See ``docs/fleet-scan/SPEC.md``.
"""
