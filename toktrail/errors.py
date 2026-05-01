from __future__ import annotations


class ToktrailError(Exception):
    """Base class for public toktrail API errors."""


class StateDatabaseError(ToktrailError):
    """The toktrail state database could not be opened or queried safely."""


class UnsupportedHarnessError(ToktrailError):
    """The requested harness is not supported."""


class SourcePathError(ToktrailError):
    """A required source path is missing, invalid, or unreadable."""


class ConfigurationError(ToktrailError):
    """The toktrail pricing/configuration file is missing, invalid, or unsupported."""


class RunNotFoundError(ToktrailError):
    """The requested tracking run does not exist."""


class NoActiveRunError(ToktrailError):
    """An operation required an active tracking run, but none exists."""


class ActiveRunExistsError(ToktrailError):
    """A new tracking run could not be started because another run is active."""


class RunAlreadyEndedError(ToktrailError):
    """The requested tracking run has already been stopped."""


class UsageImportError(ToktrailError):
    """Usage import failed after source parsing started."""


class AmbiguousSourceSessionError(ToktrailError):
    """A source-session diff found multiple candidates and needs disambiguation."""


class InvalidAPIUsageError(ToktrailError):
    """The public API was called with invalid or conflicting arguments."""


# Session-named aliases for backward compatibility
SessionNotFoundError = RunNotFoundError
NoActiveSessionError = NoActiveRunError
ActiveSessionExistsError = ActiveRunExistsError
SessionAlreadyEndedError = RunAlreadyEndedError


__all__ = [
    "ActiveRunExistsError",
    "ActiveSessionExistsError",
    "AmbiguousSourceSessionError",
    "ConfigurationError",
    "InvalidAPIUsageError",
    "NoActiveRunError",
    "NoActiveSessionError",
    "RunAlreadyEndedError",
    "RunNotFoundError",
    "SessionAlreadyEndedError",
    "SessionNotFoundError",
    "SourcePathError",
    "StateDatabaseError",
    "ToktrailError",
    "UnsupportedHarnessError",
    "UsageImportError",
]
