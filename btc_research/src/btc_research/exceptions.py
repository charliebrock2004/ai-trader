"""Errors the research engine raises. Callers should not treat these as empty results."""


class ResearchError(Exception):
    """Base class for expected research failures."""


class DataIntegrityError(ResearchError):
    """Timestamps, duplicates or OHLC relationships are not safe to use."""


class DataSourceError(ResearchError):
    """A download failed, was incomplete, or did not match the requested range."""
