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


class SessionNotFoundError(ToktrailError):
    """The requested tracking session does not exist."""


class NoActiveSessionError(ToktrailError):
    """An operation required an active tracking session, but none exists."""


class ActiveSessionExistsError(ToktrailError):
    """A new tracking session could not be started because another session is active."""


class SessionAlreadyEndedError(ToktrailError):
    """The requested tracking session has already been stopped."""


class UsageImportError(ToktrailError):
    """Usage import failed after source parsing started."""


class AmbiguousSourceSessionError(ToktrailError):
    """A source-session diff found multiple candidates and needs disambiguation."""


class InvalidAPIUsageError(ToktrailError):
    """The public API was called with invalid or conflicting arguments."""


# Run-named aliases (new primary terminology)
RunNotFoundError = SessionNotFoundError
NoActiveRunError = NoActiveSessionError
ActiveRunExistsError = ActiveSessionExistsError
RunAlreadyEndedError = SessionAlreadyEndedError


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
