import asyncio
import json
import logging
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Literal, Optional, Sequence, Union

import requests
import yaml
from cosl.coordinated_workers.nginx import CA_CERT_PATH
from juju.application import Application
from juju.unit import Unit
from minio import Minio
from pytest_operator.plugin import OpsTest
from tenacity import retry, stop_after_attempt, wait_exponential

_JUJU_DATA_CACHE = {}
_JUJU_KEYS = ("egress-subnets", "ingress-address", "private-address")
ACCESS_KEY = "accesskey"
SECRET_KEY = "secretkey"
MINIO = "minio"
BUCKET_NAME = "tempo"
S3_INTEGRATOR = "s3-integrator"
PROMETHEUS = "prometheus"
PROMETHEUS_CHARM = "prometheus-k8s"
WORKER_NAME = "tempo-worker"
APP_NAME = "tempo"
TRACEGEN_SCRIPT_PATH = Path() / "scripts" / "tracegen.py"

protocols_endpoints = {
    "jaeger_thrift_http": "{scheme}://{hostname}:14268/api/traces?format=jaeger.thrift",
    "zipkin": "{scheme}://{hostname}:9411/v1/traces",
    "jaeger_grpc": "{hostname}:14250",
    "otlp_http": "{scheme}://{hostname}:4318/v1/traces",
    "otlp_grpc": "{hostname}:4317",
}

logger = logging.getLogger(__name__)


def purge(data: dict):
    for key in _JUJU_KEYS:
        if key in data:
            del data[key]


def get_unit_info(unit_name: str, model: str = None) -> dict:
    """Return unit-info data structure.

     for example:

    traefik-k8s/0:
      opened-ports: []
      charm: local:focal/traefik-k8s-1
      leader: true
      relation-info:
      - endpoint: ingress-per-unit
        related-endpoint: ingress
        application-data:
          _supported_versions: '- v1'
        related-units:
          prometheus-k8s/0:
            in-scope: true
            data:
              egress-subnets: 10.152.183.150/32
              ingress-address: 10.152.183.150
              private-address: 10.152.183.150
      provider-id: traefik-k8s-0
      address: 10.1.232.144
    """
    cmd = f"juju show-unit {unit_name}".split(" ")
    if model:
        cmd.insert(2, "-m")
        cmd.insert(3, model)

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    raw_data = proc.stdout.read().decode("utf-8").strip()

    data = yaml.safe_load(raw_data) if raw_data else None

    if not data:
        raise ValueError(
            f"no unit info could be grabbed for {unit_name}; "
            f"are you sure it's a valid unit name?"
            f"cmd={' '.join(proc.args)}"
        )

    if unit_name not in data:
        raise KeyError(unit_name, f"not in {data!r}")

    unit_data = data[unit_name]
    _JUJU_DATA_CACHE[unit_name] = unit_data
    return unit_data


def get_relation_by_endpoint(relations, local_endpoint, remote_endpoint, remote_obj):
    matches = [
        r
        for r in relations
        if (
            (r["endpoint"] == local_endpoint and r["related-endpoint"] == remote_endpoint)
            or (r["endpoint"] == remote_endpoint and r["related-endpoint"] == local_endpoint)
        )
        and remote_obj in r["related-units"]
    ]
    if not matches:
        raise ValueError(
            f"no matches found with endpoint=="
            f"{local_endpoint} "
            f"in {remote_obj} (matches={matches})"
        )
    if len(matches) > 1:
        raise ValueError(
            "multiple matches found with endpoint=="
            f"{local_endpoint} "
            f"in {remote_obj} (matches={matches})"
        )
    return matches[0]


@dataclass
class UnitRelationData:
    unit_name: str
    endpoint: str
    leader: bool
    application_data: Dict[str, str]
    unit_data: Dict[str, str]


def get_content(
    obj: str, other_obj, include_default_juju_keys: bool = False, model: str = None
) -> UnitRelationData:
    """Get the content of the databag of `obj`, as seen from `other_obj`."""
    unit_name, endpoint = obj.split(":")
    other_unit_name, other_endpoint = other_obj.split(":")

    unit_data, app_data, leader = get_databags(
        unit_name, endpoint, other_unit_name, other_endpoint, model
    )

    if not include_default_juju_keys:
        purge(unit_data)

    return UnitRelationData(unit_name, endpoint, leader, app_data, unit_data)


def get_databags(local_unit, local_endpoint, remote_unit, remote_endpoint, model):
    """Get the databags of local unit and its leadership status.

    Given a remote unit and the remote endpoint name.
    """
    local_data = get_unit_info(local_unit, model)
    leader = local_data["leader"]

    data = get_unit_info(remote_unit, model)
    relation_info = data.get("relation-info")
    if not relation_info:
        raise RuntimeError(f"{remote_unit} has no relations")

    raw_data = get_relation_by_endpoint(relation_info, local_endpoint, remote_endpoint, local_unit)
    unit_data = raw_data["related-units"][local_unit]["data"]
    app_data = raw_data["application-data"]
    return unit_data, app_data, leader


@dataclass
class RelationData:
    provider: UnitRelationData
    requirer: UnitRelationData


def get_relation_data(
    *,
    provider_endpoint: str,
    requirer_endpoint: str,
    include_default_juju_keys: bool = False,
    model: str = None,
):
    """Get relation databags for a juju relation.

    >>> get_relation_data('prometheus/0:ingress', 'traefik/1:ingress-per-unit')
    """
    provider_data = get_content(
        provider_endpoint, requirer_endpoint, include_default_juju_keys, model
    )
    requirer_data = get_content(
        requirer_endpoint, provider_endpoint, include_default_juju_keys, model
    )
    return RelationData(provider=provider_data, requirer=requirer_data)


async def deploy_literal_bundle(ops_test: OpsTest, bundle: str):
    run_args = [
        "juju",
        "deploy",
        "--trust",
        "-m",
        ops_test.model_name,
        str(ops_test.render_bundle(bundle)),
    ]

    retcode, stdout, stderr = await ops_test.run(*run_args)
    assert retcode == 0, f"Deploy failed: {(stderr or stdout).strip()}"
    logger.info(stdout)


async def run_command(model_name: str, app_name: str, unit_num: int, command: list) -> bytes:
    cmd = ["juju", "ssh", "--model", model_name, f"{app_name}/{unit_num}", *command]
    try:
        res = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        logger.info(res)
    except subprocess.CalledProcessError as e:
        logger.error(e.stdout.decode())
        raise e
    return res.stdout


def present_facade(
    interface: str,
    app_data: Dict = None,
    unit_data: Dict = None,
    role: Literal["provide", "require"] = "provide",
    model: str = None,
    app: str = "facade",
):
    """Set up the facade charm to present this data over the interface ``interface``."""
    data = {
        "endpoint": f"{role}-{interface}",
    }
    if app_data:
        data["app_data"] = json.dumps(app_data)
    if unit_data:
        data["unit_data"] = json.dumps(unit_data)

    with tempfile.NamedTemporaryFile(dir=os.getcwd()) as f:
        fpath = Path(f.name)
        fpath.write_text(yaml.safe_dump(data))

        _model = f" --model {model}" if model else ""

        subprocess.run(shlex.split(f"juju run {app}/0{_model} update --params {fpath.absolute()}"))


async def get_unit_address(ops_test: OpsTest, app_name, unit_no):
    status = await ops_test.model.get_status()
    app = status["applications"][app_name]
    if app is None:
        assert False, f"no app exists with name {app_name}"
    unit = app["units"].get(f"{app_name}/{unit_no}")
    if unit is None:
        assert False, f"no unit exists in app {app_name} with index {unit_no}"
    return unit["address"]


async def deploy_and_configure_minio(ops_test: OpsTest):
    config = {
        "access-key": ACCESS_KEY,
        "secret-key": SECRET_KEY,
    }
    await ops_test.model.deploy(MINIO, channel="edge", trust=True, config=config)
    await ops_test.model.wait_for_idle(apps=[MINIO], status="active", timeout=2000)
    minio_addr = await get_unit_address(ops_test, MINIO, "0")

    mc_client = Minio(
        f"{minio_addr}:9000",
        access_key="accesskey",
        secret_key="secretkey",
        secure=False,
    )

    # create tempo bucket
    found = mc_client.bucket_exists(BUCKET_NAME)
    if not found:
        mc_client.make_bucket(BUCKET_NAME)

    # configure s3-integrator
    s3_integrator_app: Application = ops_test.model.applications[S3_INTEGRATOR]
    s3_integrator_leader: Unit = s3_integrator_app.units[0]

    await s3_integrator_app.set_config(
        {
            "endpoint": f"minio-0.minio-endpoints.{ops_test.model.name}.svc.cluster.local:9000",
            "bucket": BUCKET_NAME,
        }
    )

    action = await s3_integrator_leader.run_action("sync-s3-credentials", **config)
    action_result = await action.wait()
    assert action_result.status == "completed"


def tempo_worker_charm_and_channel():
    """Tempo worker charm used for integration testing.

    Build once per session and reuse it in all integration tests to save some minutes/hours.
    You can also set `TEMPO_WORKER_CHARM` env variable to use an already existing built charm.
    """
    if path_from_env := os.getenv("TEMPO_WORKER_CHARM"):
        return Path(path_from_env).absolute(), None
    return "tempo-worker-k8s", "edge"


def get_resources(path: Union[str, Path]):
    meta = yaml.safe_load((Path(path) / "charmcraft.yaml").read_text())
    resources_meta = meta.get("resources", {})
    return {res_name: res_meta["upstream-source"] for res_name, res_meta in resources_meta.items()}


async def deploy_monolithic_cluster(ops_test: OpsTest, tempo_app=APP_NAME):
    """This assumes tempo-coordinator is already deployed as `param:tempo_app`."""
    tempo_worker_charm_url, channel = tempo_worker_charm_and_channel()
    await ops_test.model.deploy(
        tempo_worker_charm_url,
        application_name=WORKER_NAME,
        channel=channel,
        trust=True,
    )
    await ops_test.model.deploy(S3_INTEGRATOR, channel="edge")

    await ops_test.model.integrate(tempo_app + ":s3", S3_INTEGRATOR + ":s3-credentials")
    await ops_test.model.integrate(tempo_app + ":tempo-cluster", WORKER_NAME + ":tempo-cluster")

    await deploy_and_configure_minio(ops_test)
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[tempo_app, WORKER_NAME, S3_INTEGRATOR],
            status="active",
            timeout=2000,
            idle_period=30,
        )


async def deploy_distributed_cluster(ops_test: OpsTest, roles: Sequence[str], tempo_app=APP_NAME):
    """This assumes tempo-coordinator is already deployed as `param:tempo_app`."""
    tempo_worker_charm_url, channel = tempo_worker_charm_and_channel()

    await asyncio.gather(
        *(
            ops_test.model.deploy(
                tempo_worker_charm_url,
                application_name=f"{WORKER_NAME}-{role}",
                channel=channel,
                trust=True,
                config={"role-all": False, f"role-{role}": True},
            )
            for role in roles
        ),
        ops_test.model.deploy(S3_INTEGRATOR, channel="edge"),
        ops_test.model.deploy(
            PROMETHEUS_CHARM, application_name=PROMETHEUS, channel="edge", trust=True
        ),
    )

    all_workers = tuple(f"{WORKER_NAME}-{role}" for role in roles)
    await asyncio.gather(
        *(
            ops_test.model.integrate(tempo_app + ":tempo-cluster", worker_name + ":tempo-cluster")
            for worker_name in all_workers
        ),
        ops_test.model.integrate(tempo_app + ":send-remote-write", PROMETHEUS),
        ops_test.model.integrate(tempo_app + ":s3", S3_INTEGRATOR + ":s3-credentials"),
    )

    await deploy_and_configure_minio(ops_test)
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[tempo_app, *all_workers, S3_INTEGRATOR, PROMETHEUS],
            status="active",
            timeout=2000,
            idle_period=30,
        )


def get_traces(tempo_host: str, service_name="tracegen", tls=True):
    url = f"{'https' if tls else 'http'}://{tempo_host}:3200/api/search?tags=service.name={service_name}"
    req = requests.get(
        url,
        verify=False,
    )
    assert req.status_code == 200
    traces = json.loads(req.text)["traces"]
    return traces


@retry(stop=stop_after_attempt(15), wait=wait_exponential(multiplier=1, min=4, max=10))
async def get_traces_patiently(tempo_host, service_name="tracegen", tls=True):
    logger.info(f"polling {tempo_host} for service {service_name!r} traces...")
    traces = get_traces(tempo_host, service_name=service_name, tls=tls)
    assert len(traces) > 0
    return traces


async def emit_trace(
    endpoint,
    ops_test: OpsTest,
    nonce: str = None,
    proto: str = "otlp_http",
    service_name: Optional[str] = "tracegen",
    verbose=0,
    use_cert=False,
):
    """Use juju ssh to run tracegen from the tempo charm; to avoid any DNS issues."""
    # SCP tracegen script onto unit and install dependencies
    logger.info(f"pushing tracegen onto {APP_NAME}/0")

    await ops_test.juju("scp", TRACEGEN_SCRIPT_PATH, f"{APP_NAME}/0:tracegen.py")
    await ops_test.juju(
        "ssh",
        f"{APP_NAME}/0",
        "python3 -m pip install protobuf==3.20.* opentelemetry-exporter-otlp-proto-grpc opentelemetry-exporter-otlp-proto-http"
        + " opentelemetry-exporter-zipkin opentelemetry-exporter-jaeger",
    )

    cmd = (
        f"juju ssh -m {ops_test.model_name} {APP_NAME}/0 "
        f"TRACEGEN_SERVICE={service_name or ''} "
        f"TRACEGEN_ENDPOINT={endpoint} "
        f"TRACEGEN_VERBOSE={verbose} "
        f"TRACEGEN_PROTOCOL={proto} "
        f"TRACEGEN_CERT={CA_CERT_PATH if use_cert else ''} "
        f"TRACEGEN_NONCE={nonce or ''} "
        "python3 tracegen.py"
    )

    logger.info(f"running tracegen with {cmd!r}")

    out = subprocess.run(shlex.split(cmd), text=True, capture_output=True).stdout
    logger.info(f"tracegen completed; stdout={out!r}")


async def get_application_ip(ops_test: OpsTest, app_name: str):
    status = await ops_test.model.get_status()
    app = status["applications"][app_name]
    return app.public_address


def _get_endpoint(protocol: str, hostname: str, tls: bool):
    protocol_endpoint = protocols_endpoints.get(protocol)
    if protocol_endpoint is None:
        assert False, f"Invalid {protocol}"

    if "grpc" in protocol:
        # no scheme in _grpc endpoints
        return protocol_endpoint.format(hostname=hostname)
    else:
        return protocol_endpoint.format(hostname=hostname, scheme="https" if tls else "http")


def get_tempo_ingressed_endpoint(hostname, protocol: str, tls: bool):
    return _get_endpoint(protocol, hostname, tls)


def get_tempo_internal_endpoint(ops_test: OpsTest, protocol: str, tls: bool):
    hostname = f"{APP_NAME}-0.{APP_NAME}-endpoints.{ops_test.model.name}.svc.cluster.local"
    return _get_endpoint(protocol, hostname, tls)


async def get_tempo_application_endpoint(tempo_ip: str, protocol: str, tls: bool):
    return _get_endpoint(protocol, tempo_ip, tls)
