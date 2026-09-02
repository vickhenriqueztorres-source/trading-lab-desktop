from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apps.iqoption_worker.order_session import IQOptionOrderSession
    from apps.iqoption_worker.reconciliation import IQOptionReconciliationHandler
    from apps.iqoption_worker.schema import IQOptionErrorCategory, IQOptionWorkerError
    from apps.iqoption_worker.server import IQOptionWorkerServer

__all__ = [
    "IQOptionErrorCategory",
    "IQOptionOrderSession",
    "IQOptionReconciliationHandler",
    "IQOptionWorkerError",
    "IQOptionWorkerServer",
]


def __getattr__(name: str) -> Any:
    """Load public worker types lazily to avoid package bootstrap cycles."""

    if name in {"IQOptionErrorCategory", "IQOptionWorkerError"}:
        from apps.iqoption_worker import schema

        return getattr(schema, name)
    if name == "IQOptionOrderSession":
        from apps.iqoption_worker.order_session import IQOptionOrderSession

        return IQOptionOrderSession
    if name == "IQOptionReconciliationHandler":
        from apps.iqoption_worker.reconciliation import IQOptionReconciliationHandler

        return IQOptionReconciliationHandler
    if name == "IQOptionWorkerServer":
        from apps.iqoption_worker.server import IQOptionWorkerServer

        return IQOptionWorkerServer
    raise AttributeError(name)
