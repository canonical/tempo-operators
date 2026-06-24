"""Tests for charm_tracing library compatibility across Python versions."""

import re
from importlib.metadata import distributions


def _normalize_name(name: str) -> str:
    """Normalize package name to underscore format (matching importlib_metadata behavior).

    This is a copy of the function used in charm_tracing.py for testing purposes.
    """
    return re.sub(r"[-_.]+", "_", name).lower()


def test_distribution_name_is_accessible():
    """Verify that distribution.name is accessible for all installed distributions.

    This tests the fix for https://github.com/canonical/tempo-operators/issues/355
    where accessing distribution._normalized_name failed on Python 3.12 because
    the stdlib importlib.metadata doesn't expose this private attribute.

    The fix uses the public distribution.name API instead.
    """
    for distribution in distributions():
        # This should not raise AttributeError
        name = distribution.name
        assert isinstance(name, str)
        assert len(name) > 0


def test_normalize_name_underscore_format():
    """Test name normalization matches importlib_metadata._normalized_name behavior.

    The original code used distribution._normalized_name which normalizes to underscores.
    We replicate this behavior using distribution.name + manual normalization.
    """
    # All separators (hyphens, underscores, dots) should become underscores
    assert _normalize_name("opentelemetry_sdk") == "opentelemetry_sdk"
    assert _normalize_name("opentelemetry-sdk") == "opentelemetry_sdk"
    assert _normalize_name("opentelemetry.sdk") == "opentelemetry_sdk"
    assert _normalize_name("OpenTelemetry_SDK") == "opentelemetry_sdk"
    # Multiple consecutive separators should collapse to single underscore
    assert _normalize_name("OpenTelemetry__SDK") == "opentelemetry_sdk"
    assert _normalize_name("opentelemetry---sdk") == "opentelemetry_sdk"
    assert _normalize_name("opentelemetry_..sdk") == "opentelemetry_sdk"


def test_opentelemetry_distributions_can_be_normalized():
    """Verify opentelemetry packages can be found using the normalization logic."""
    otel_distributions = []
    for distribution in distributions():
        name = _normalize_name(distribution.name)
        if name.startswith("opentelemetry_"):
            otel_distributions.append(name)

    # We should find at least one opentelemetry package in the test environment
    # since the charm depends on opentelemetry-exporter-otlp-proto-http
    assert len(otel_distributions) > 0, "Expected to find opentelemetry distributions"
