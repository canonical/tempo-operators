"""Tests for charm_tracing library compatibility across Python versions."""

import importlib.metadata
from types import SimpleNamespace
from unittest.mock import patch

from charms.tempo_coordinator_k8s.v0 import charm_tracing


class _Dist:
    """Fake distribution without a `.name` attribute (python<3.10) and with no files."""

    files = None

    def __init__(self, name, path):
        self.metadata = {"Name": name} if name else None
        self._path = path


def test_remove_stale_packages_without_distribution_name(monkeypatch, tmp_path):
    # GIVEN a stale and a healthy distribution of the same otel package, none exposing `.name`
    stale = tmp_path / "opentelemetry_sdk-1.0.dist-info"
    stale.mkdir()
    healthy = SimpleNamespace(
        metadata={"Name": "opentelemetry-sdk"}, files=["x"], _path=tmp_path / "other"
    )
    dists = [_Dist("opentelemetry-sdk", stale), healthy, _Dist(None, tmp_path), _Dist("foo", tmp_path)]
    monkeypatch.setenv("JUJU_DISPATCH_PATH", "hooks/upgrade-charm")

    # WHEN the patch runs on upgrade-charm
    with patch.object(importlib.metadata, "distributions", return_value=dists):
        charm_tracing._remove_stale_otel_sdk_packages()

    # THEN it doesn't crash and only the empty duplicate is removed
    assert not stale.exists()
    assert tmp_path.exists()


def test_remove_stale_packages_noop_outside_upgrade(monkeypatch):
    monkeypatch.setenv("JUJU_DISPATCH_PATH", "hooks/start")
    with patch.object(importlib.metadata, "distributions", side_effect=AssertionError):
        charm_tracing._remove_stale_otel_sdk_packages()
