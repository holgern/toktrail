"""Compatibility facade for DB APIs during staged refactor."""

from __future__ import annotations

from importlib import import_module

from toktrail._db.areas import *  # noqa: F401,F403
from toktrail._db.connection import *  # noqa: F401,F403
from toktrail._db.migrations import *  # noqa: F401,F403
from toktrail._db.reports_areas import *  # noqa: F401,F403
from toktrail._db.reports_runs import *  # noqa: F401,F403
from toktrail._db.reports_series import *  # noqa: F401,F403
from toktrail._db.reports_sessions import *  # noqa: F401,F403
from toktrail._db.reports_subscriptions import *  # noqa: F401,F403
from toktrail._db.reports_usage import *  # noqa: F401,F403
from toktrail._db.runs import *  # noqa: F401,F403
from toktrail._db.schema import *  # noqa: F401,F403
from toktrail._db.source_sessions import *  # noqa: F401,F403
from toktrail._db.usage_events import *  # noqa: F401,F403

_impl = import_module("toktrail._db._impl_db")


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    return getattr(_impl, name)


__all__ = [name for name in vars(_impl) if not name.startswith("__")]
