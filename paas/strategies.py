"""
Target selection logic for the PaaS deployment view.

The view can use any subclass of TargetSelectionStrategy to decide which
RemoteHost a normal user should deploy to.  Super‑users can still pick
manually – the strategy is only applied when ``request.user.is_superuser``
is False.
"""

import logging
from abc import ABC, abstractmethod
from typing import Iterable, List

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import models
from django.http import HttpRequest

# Make sure the import path is correct for your RemoteHost model
from .models import RemoteHost

log = logging.getLogger(__name__)


def _allowed_hosts(hosts: Iterable[RemoteHost]) -> List[RemoteHost]:
    """
    Gibt nur Hosts zurück, die *nicht* mit `nur_superuser=True` gekennzeichnet sind.
    Arbeitet sowohl mit QuerySets als auch mit Listen/iterables.
    """
    if isinstance(hosts, models.QuerySet):
        return list(hosts.filter(nur_superuser=False))
    return [h for h in hosts if not getattr(h, "nur_superuser", False)]


# ------------------------------------------------------------------
# Abstract base class
# ------------------------------------------------------------------
class TargetSelectionStrategy(ABC):
    """
    Abstract base class for selecting a deployment target.

    Sub‑classes must implement :meth:`select_target`.  The method receives the
    current :class:`~django.http.HttpRequest`, the :class:`~django.contrib.auth.models.User`
    and a *queryset* of all available :class:`~RemoteHost` instances.

    The return value must be **one** :class:`RemoteHost` instance, or ``None`` if
    no suitable host is found (the caller should handle the ``None`` case
    gracefully, e.g. by showing an error message).
    """

    @abstractmethod
    def select_target(
        self,
        request: "HttpRequest",
        user: "User",
        hosts: Iterable[RemoteHost],
    ) -> RemoteHost | None:
        """
        Return a single RemoteHost suitable for the deployment.

        Parameters
        ----------
        request : HttpRequest
            The current request – may contain useful meta‑data.
        user : User
            The user that initiated the deployment.
        hosts : Iterable[RemoteHost]
            The list / queryset of candidate hosts.
        """
        raise NotImplementedError


# ------------------------------------------------------------------
# Concrete strategy 1 – Round Robin
# ------------------------------------------------------------------
class RoundRobinStrategy(TargetSelectionStrategy):
    """
    Round‑robin selection.

    A simple counter stored in Django’s cache is used to remember the last
    host that was selected.  The counter is advanced modulo ``len(hosts)``.
    If the counter is missing or hosts changed (e.g. a host was deleted),
    the counter is reset to 0.

    The cache key is deterministic per “cluster” – you can extend it to
    support multiple clusters by adding a ``cluster_name`` argument if needed.
    """

    CACHE_KEY = "paas_rr_selection_index"
    CACHE_TIMEOUT = 60 * 60  # 1 hour – you can keep it forever too

    def _get_current_index(self, num_hosts: int) -> int:
        """
        Retrieve the current round‑robin index from cache, reset if out of range.
        """
        idx = cache.get(self.CACHE_KEY)
        if idx is None or not isinstance(idx, int) or idx >= num_hosts:
            idx = 0
        return idx

    def _set_current_index(self, idx: int) -> None:
        cache.set(self.CACHE_KEY, idx, timeout=self.CACHE_TIMEOUT)

    def select_target(
        self,
        request: "HttpRequest",
        user: "User",
        hosts: Iterable[RemoteHost],
    ) -> RemoteHost | None:
        # ---------- 3.1.1  Filter Hosts -------------
        host_list = _allowed_hosts(hosts)
        if not host_list:
            log.warning("RoundRobinStrategy: Keine erlaubten Hosts vorhanden")
            return None

        idx = self._get_current_index(len(host_list))
        chosen = host_list[idx]
        next_idx = (idx + 1) % len(host_list)
        self._set_current_index(next_idx)

        log.debug(
            "RoundRobinStrategy: user=%s selected host=%s (index=%d)",
            user.username,
            chosen.hostname,
            idx,
        )
        return chosen


# ------------------------------------------------------------------
# Concrete strategy 2 – Least Load
# ------------------------------------------------------------------
class LeastLoadStrategy(TargetSelectionStrategy):
    """
    Select the host with the smallest `current_load` value.

    The RemoteHost model must expose a numeric field named ``current_load``.
    If the field is missing, the strategy falls back to round‑robin.
    """

    def select_target(
        self,
        request: "HttpRequest",
        user: "User",
        hosts: Iterable[RemoteHost],
    ) -> RemoteHost | None:
        # ---------- 3.2.1  Filter Hosts -------------
        allowed = _allowed_hosts(hosts)

        # Falls wir noch ein QuerySet haben, können wir die Sortierung in DB‑Level
        # ausführen.  Ansonsten nutzen wir Python‑min().
        chosen = None

        if isinstance(allowed, models.QuerySet):
            # Order by current_load – und nur erlaubte Hosts
            qs = allowed.order_by("current_load")
            chosen = qs.first()
        else:
            # List / iterable
            try:
                chosen = min(allowed, key=lambda h: getattr(h, "current_load", float("inf")))
            except Exception as exc:
                log.exception("LeastLoadStrategy: Fehler bei min(): %s", exc)

        if chosen:
            log.debug(
                "LeastLoadStrategy: user=%s selected host=%s (load=%s)",
                user.username,
                chosen.hostname,
                getattr(chosen, "current_load", "unknown"),
            )
        else:
            log.warning("LeastLoadStrategy: Keine erlaubten Hosts verfügbar")

        return chosen