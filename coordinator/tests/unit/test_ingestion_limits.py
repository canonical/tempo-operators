import copy
from dataclasses import asdict
from pathlib import Path

import pytest
import yaml
from scenario import State

from charm import INGESTION_LIMIT_OPTIONS
from tempo import IngestionLimits, Tempo
from tests.unit.helpers import get_tempo_config

# The overrides Tempo gets with default config (Tempo's own built-in defaults).
DEFAULT_OVERRIDES = {
    "ingestion": {
        "rate_limit_bytes": 15000000,
        "burst_size_bytes": 20000000,
        "max_traces_per_user": 10000,
    },
    "global": {"max_bytes_per_trace": 5000000},
}
PROCESSORS = ["span-metrics", "service-graphs", "local-blocks"]


def _defaults(limits=None, metrics_generator_enabled=False):
    overrides = Tempo(720, lambda: [], ingestion_limits=limits)._build_overrides_config(
        metrics_generator_enabled=metrics_generator_enabled
    )
    return overrides.model_dump(mode="json", by_alias=True, exclude_none=True)[
        "defaults"
    ]


def test_overrides_include_all_limits():
    assert _defaults() == {
        "ingestion": {
            "rate_limit_bytes": 15000000,
            "burst_size_bytes": 20000000,
            "max_traces_per_user": 10000,
        },
        "global": {"max_bytes_per_trace": 5000000},
    }


def test_overrides_skip_unset_limits():
    defaults = _defaults(
        IngestionLimits(rate_limit_bytes=None, max_bytes_per_trace=None)
    )
    assert defaults["ingestion"] == {
        "burst_size_bytes": 20000000,
        "max_traces_per_user": 10000,
    }
    assert "global" not in defaults


def test_overrides_none_when_nothing_to_set():
    tempo = Tempo(
        720, lambda: [], ingestion_limits=IngestionLimits(None, None, None, None)
    )
    assert tempo._build_overrides_config(metrics_generator_enabled=False) is None


def test_overrides_keep_metrics_generator_processors():
    assert _defaults(metrics_generator_enabled=True) == {
        **DEFAULT_OVERRIDES,
        "metrics_generator": {"processors": PROCESSORS},
    }


def test_overrides_keep_zero():
    defaults = _defaults(IngestionLimits(max_traces_per_user=0, max_bytes_per_trace=0))
    assert defaults["ingestion"]["max_traces_per_user"] == 0
    assert defaults["global"]["max_bytes_per_trace"] == 0


@pytest.fixture
def run(
    context, all_worker, s3, peer, nginx_container, nginx_prometheus_exporter_container
):
    def _run(config=None, extra_relations=()):
        state = State(
            leader=True,
            config=config or {},
            relations=[peer, s3, all_worker, *extra_relations],
            containers=[nginx_container, nginx_prometheus_exporter_container],
        )
        return context.run(context.on.relation_changed(all_worker), state)

    return _run


def _overrides(state_out):
    return yaml.safe_load(get_tempo_config(state_out))["overrides"]["defaults"]


def test_charmcraft_defaults_match_ingestion_limits():
    charmcraft = Path(__file__).parents[2] / "charmcraft.yaml"
    options = yaml.safe_load(charmcraft.read_text())["config"]["options"]
    for option, (field, _) in INGESTION_LIMIT_OPTIONS.items():
        assert options[option]["type"] == "int"
        assert options[option]["default"] == asdict(IngestionLimits())[field]


def test_charm_writes_default_limits(run):
    defaults = _overrides(run())
    assert defaults["ingestion"] == {
        "rate_limit_bytes": 15000000,
        "burst_size_bytes": 20000000,
        "max_traces_per_user": 10000,
    }
    assert defaults["global"] == {"max_bytes_per_trace": 5000000}


def test_charm_writes_custom_limits(run):
    state_out = run(
        {
            "ingestion_rate_limit_bytes": 2000000,
            "ingestion_burst_size_bytes": 4000000,
            "max_traces_per_user": 500,
            "max_bytes_per_trace": 1000000,
        }
    )
    defaults = _overrides(state_out)
    assert defaults["ingestion"] == {
        "rate_limit_bytes": 2000000,
        "burst_size_bytes": 4000000,
        "max_traces_per_user": 500,
    }
    assert defaults["global"] == {"max_bytes_per_trace": 1000000}
    assert state_out.unit_status.name == "active"


def test_charm_accepts_zero_for_trace_limits(run):
    state_out = run({"max_traces_per_user": 0, "max_bytes_per_trace": 0})
    defaults = _overrides(state_out)
    assert defaults["ingestion"]["max_traces_per_user"] == 0
    assert defaults["global"]["max_bytes_per_trace"] == 0
    assert state_out.unit_status.name == "active"


@pytest.mark.parametrize(
    "option, value, section, key",
    [
        ("ingestion_rate_limit_bytes", 0, "ingestion", "rate_limit_bytes"),
        ("ingestion_rate_limit_bytes", -1, "ingestion", "rate_limit_bytes"),
        ("ingestion_burst_size_bytes", 0, "ingestion", "burst_size_bytes"),
        ("max_traces_per_user", -1, "ingestion", "max_traces_per_user"),
        ("max_bytes_per_trace", -1, "global", "max_bytes_per_trace"),
    ],
)
def test_charm_invalid_limit_is_left_out_and_blocks(run, option, value, section, key):
    state_out = run({option: value})
    # only the bad key is left out: every other limit keeps its value
    expected = copy.deepcopy(DEFAULT_OVERRIDES)
    del expected[section][key]
    if not expected[section]:
        del expected[section]
    assert _overrides(state_out) == expected
    assert state_out.unit_status.name == "blocked"
    assert state_out.unit_status.message == (
        f"Invalid config: {option} out of range; Tempo defaults used"
    )


def test_charm_reports_all_invalid_limits(run):
    state_out = run({"ingestion_rate_limit_bytes": 0, "max_bytes_per_trace": -1})
    assert state_out.unit_status.message == (
        "Invalid config: ingestion_rate_limit_bytes, max_bytes_per_trace out of range;"
        " Tempo defaults used"
    )


def test_charm_keeps_limits_with_metrics_generator(run, remote_write):
    assert _overrides(run(extra_relations=(remote_write,))) == {
        **DEFAULT_OVERRIDES,
        "metrics_generator": {"processors": PROCESSORS},
    }
