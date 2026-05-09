"""Sample Python file that is clean — no violations."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def fetch_data(query: str) -> str:
    """Fetch data for the given query."""
    logger.info("Fetching: %s", query)
    return query.strip()


def process(data: str) -> str:
    """Process the data."""
    return data.upper()
