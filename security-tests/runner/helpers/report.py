"""Human-readable PASS/FAIL reporting.

The customer running this suite should be able to read the output without
knowing pytest. Every check prints one plain sentence saying what was proven.
"""
from __future__ import annotations

import logging

_LOG = logging.getLogger("security-tests")

_PASS = "PASS"
_FAIL = "FAIL"
_INFO = "INFO"


def _emit(status: str, check: str, statement: str) -> str:
    line = f"[{status}] {check}: {statement}"
    _LOG.info(line)
    return line


def passed(check: str, statement: str) -> str:
    """Record a check that held, in the words we would show a customer."""
    return _emit(_PASS, check, statement)


def failed(check: str, statement: str) -> str:
    """Record a check that did not hold."""
    return _emit(_FAIL, check, statement)


def note(check: str, statement: str) -> str:
    """Record something the suite states but does not prove on this target."""
    return _emit(_INFO, check, statement)
