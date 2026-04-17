import json
import logging
import os
import shlex
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import List, Literal, Optional, Sequence, Set, cast

import jubilant
import requests
import yaml
from coordinated_workers.nginx import CA_CERT_PATH
from jubilant import Juju, all_active
from lightkube import Client
from lightkube.generic_resource import create_namespaced_resource
from tenacity import retry, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]


def pack(root: Path | str = "./", platform: str | None = None) -> Path:
    """Pack a local charm and return the path to the packed .charm file."""
    platform_arg = f" --platform {platform}" if platform else ""
    cmd = f"charmcraft pack -p {root}{platform_arg}"
    proc = subprocess.run(
        shlex.split(cmd),
        check=True,
        capture_output=True,
        text=True,
    )
    # charmcraft prints "Packed <filename>" lines to stderr
    packed_charms = [
        line.split()[1]
        for line in proc.stderr.strip().splitlines()
        if line.startswith("Packed")
    ]
    if not packed_charms:
        raise ValueError(
            f"unable to get packed charm(s) ({cmd!r} completed with "
            f"{proc.returncode=}, {proc.stdout=}, {proc.stderr=})"
        )
    if len(packed_charms) > 1:
        raise ValueError(
            "This charm supports multiple platforms. "
            "Pass a `platform` argument to control which charm you're getting instead."
        )
    return Path(packed_charms[0]).resolve()


def get_resources(root: Path | str = "./") -> dict[str, str] | None:
    """Obtain charm resources from metadata.yaml or charmcraft.yaml upstream-source fields."""
    for meta_name in ("metadata.yaml", "charmcraft.yaml"):
        meta_path = Path(root) / meta_name
        if meta_path.exists():
            meta = yaml.safe_load(meta_path.read_text())
            if meta_resources := meta.get("resources"):
                return {
                    resource: res_meta["upstream-source"]
                    for resource, res_meta in meta_resources.items()
                }
            logger.info("resources not found in %s; proceeding without resources", meta_name)
            return None
    logger.error("metadata/charmcraft.yaml not found at %s; unable to load resources", root)
    return None

CI_TRUE_VALUES = {"1", "true", "yes"}
COORDINATOR_CHARM_FILENAME = "tempo-coordinator-k8s_ubuntu@24.04-amd64.charm"
WORKER_CHARM_FILENAME = "tempo-worker-k8s_ubuntu@24.04-amd64.charm"

TRACEGEN_SCRIPT_PATH = REPO_ROOT / "coordinator" / "scripts" / "tracegen.py"
INTEGRATION_TESTERS_CHANNEL = "2/edge"

# Application names used uniformly across the tests
PROMETHEUS_APP = "prometheus"
S3_APP = "seaweedfs"
WORKER_APP = "tempo-worker"
TEMPO_APP = "tempo"
SSC_APP = "ssc"
TRAEFIK_APP = "trfk"
ISTIO_APP = "istio-k8s"
ISTIO_BEACON_APP = "istio-beacon-k8s"
ISTIO_INGRESS_APP = "istio-ingress-k8s"

ALL_ROLES = [
    "querier",
    "query_frontend",
    "ingester",
    "distributor",
    "compactor",
    "metrics_generator",
]
ALL_WORKERS = [f"{WORKER_APP}-" + role.replace("_", "-") for role in ALL_ROLES]

protocols_endpoints = {
    "jaeger_thrift_http": "{scheme}://{hostname}:14268/api/traces?format=jaeger.thrift",
    "zipkin": "{scheme}://{hostname}:9411/v1/traces",
    "jaeger_grpc": "{hostname}:14250",
    "otlp_http": "{scheme}://{hostname}:4318/v1/traces",
    "otlp_grpc": "{hostname}:4317",
}

api_endpoints = {
    "tempo_http": "{scheme}://{hostname}:3200/api",
    "tempo_grpc": "{hostname}:9096",
}


def _ci_enabled() -> bool:
    return os.getenv("CI", "").strip().lower() in CI_TRUE_VALUES


def _set_ci_charm_paths_if_unset() -> None:
    if not _ci_enabled():
        return

    coordinator_path = Path.cwd() / COORDINATOR_CHARM_FILENAME
    worker_path = Path.cwd() / WORKER_CHARM_FILENAME

    if coordinator_path.is_file() and not os.getenv("COORDINATOR_CHARM_PATH"):
        os.environ["COORDINATOR_CHARM_PATH"] = str(coordinator_path)
    if worker_path.is_file() and not os.getenv("WORKER_CHARM_PATH"):
        os.environ["WORKER_CHARM_PATH"] = str(worker_path)


def run_command(model_name: str, app_name: str, unit_num: int, command: list) -> bytes:
    cmd = ["juju", "ssh", "--model", model_name, f"{app_name}/{unit_num}", *command]
    try:
        res = subprocess.run(
            cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        logger.info(res)
    except subprocess.CalledProcessError as exc:
        logger.error(exc.stdout.decode())
        raise exc
    return res.stdout


def get_app_ip_address(juju: Juju, app_name: str):
    """Return a juju application's IP address."""
    return juju.status().apps[app_name].address


def get_unit_ip_address(juju: Juju, app_name: str, unit_no: int):
    """Return a juju unit's IP address."""
    return juju.status().apps[app_name].units[f"{app_name}/{unit_no}"].address


def charm_and_channel_and_resources(
    role: Literal["coordinator", "worker"], charm_path_key: str, charm_channel_key: str
):
    """Tempo coordinator or worker charm used for integration testing.

    Build once per session and reuse it in all integration tests to save some minutes/hours.
    """
    _set_ci_charm_paths_if_unset()
    # deploy charm from charmhub
    if channel_from_env := os.getenv(charm_channel_key):
        charm = f"tempo-{role}-k8s"
        logger.info("Using published %s charm from %s", charm, channel_from_env)
        return charm, channel_from_env, None
    # else deploy from a charm packed locally
    if path_from_env := os.getenv(charm_path_key):
        charm_path = Path(path_from_env).absolute()
        logger.info("Using local %s charm: %s", role, charm_path)
        # Ensure we read resources from the charm source directory for the
        # requested role, rather than from the parent of a packed charm file
        # which may be the repository root and contain a different charm's
        # metadata.
        return (
            charm_path,
            None,
            get_resources(REPO_ROOT / role),
        )
    # else try to pack the charm
    for _ in range(3):
        logger.info("packing Tempo %s charm...", role)
        try:
            pth = pack(REPO_ROOT / role)
        except subprocess.CalledProcessError:
            logger.warning("Failed to build Tempo %s. Trying again!", role)
            continue
        os.environ[charm_path_key] = str(pth)
        return pth, None, get_resources(REPO_ROOT / role)
    raise subprocess.CalledProcessError(1, f"pack {role}")


def deploy_tempo(juju: Juju, name: str = TEMPO_APP):
    charm_url, channel, resources = charm_and_channel_and_resources(
        "coordinator", "COORDINATOR_CHARM_PATH", "COORDINATOR_CHARM_CHANNEL"
    )
    juju.deploy(
        charm_url,
        name,
        channel=channel,
        resources=resources,
        trust=True,
    )


def deploy_s3(juju: Juju, s3: str = S3_APP, wait_for_idle: bool = True):
    if s3 not in juju.status().apps:
        juju.deploy("seaweedfs-k8s", s3, channel="edge")

    if wait_for_idle:
        juju.wait(
            lambda status: jubilant.all_active(status, s3),
            timeout=2000,
            delay=5,
            successes=3,
        )


def _deploy_cluster(
    juju: Juju,
    workers: Sequence[str],
    s3: str = S3_APP,
    coordinator: str = TEMPO_APP,
    wait_for_idle: bool = True,
):
    if coordinator not in juju.status().apps:
        deploy_tempo(juju, name=coordinator)

    for worker in workers:
        juju.integrate(coordinator, worker)
        # if we have an explicit metrics generator worker, we need to integrate with prometheus not to be in blocked
        if "metrics-generator" in worker:
            juju.integrate(
                PROMETHEUS_APP + ":receive-remote-write",
                coordinator + ":send-remote-write",
            )

    deploy_s3(juju, s3=s3, wait_for_idle=False)
    juju.integrate(coordinator, s3)

    if wait_for_idle:
        juju.wait(
            lambda status: jubilant.all_active(status, coordinator, *workers, s3),
            timeout=2000,
            delay=5,
            successes=3,
        )


def deploy_monolithic_cluster(
    juju: Juju,
    worker: str = WORKER_APP,
    s3: str = S3_APP,
    coordinator: str = TEMPO_APP,
    wait_for_idle: bool = True,
):
    """Deploy a tempo monolithic cluster."""
    tempo_worker_charm_url, channel, resources = charm_and_channel_and_resources(
        "worker", "WORKER_CHARM_PATH", "WORKER_CHARM_CHANNEL"
    )
    juju.deploy(
        tempo_worker_charm_url,
        app=worker,
        channel=channel,
        trust=True,
        resources=resources,
    )
    _deploy_cluster(
        juju,
        [worker],
        coordinator=coordinator,
        s3=s3,
        wait_for_idle=wait_for_idle,
    )


def deploy_prometheus(juju: Juju):
    """Deploy a pinned revision of prometheus that we know to work."""
    juju.deploy(
        "prometheus-k8s",
        app=PROMETHEUS_APP,
        revision=254,  # what's on 2/edge at July 17, 2025.
        channel=INTEGRATION_TESTERS_CHANNEL,
        trust=True,
    )


def deploy_istio(juju: Juju):
    """Deploy Istio service mesh."""
    juju.deploy(
        "istio-k8s",
        app=ISTIO_APP,
        channel=INTEGRATION_TESTERS_CHANNEL,
        trust=True,
    )


def deploy_istio_beacon(juju: Juju):
    """Deploy Istio beacon for ambient mode support."""
    juju.deploy(
        "istio-beacon-k8s",
        app=ISTIO_BEACON_APP,
        channel=INTEGRATION_TESTERS_CHANNEL,
        trust=True,
    )


def deploy_distributed_cluster(
    juju: Juju,
    roles: Sequence[str],
    worker: str = WORKER_APP,
    coordinator: str = TEMPO_APP,
    s3: str = S3_APP,
):
    """Deploy a tempo distributed cluster."""
    tempo_worker_charm_url, channel, resources = charm_and_channel_and_resources(
        "worker", "WORKER_CHARM_PATH", "WORKER_CHARM_CHANNEL"
    )

    all_workers = []

    for role in roles:
        role_sanitized = role.replace("_", "-")
        worker_name = f"{worker}-{role_sanitized}"
        all_workers.append(worker_name)

        juju.deploy(
            tempo_worker_charm_url,
            app=worker_name,
            channel=channel,
            trust=True,
            config={"role-all": False, f"role-{role_sanitized}": True},
            resources=resources,
        )

        if role_sanitized == "metrics-generator":
            deploy_prometheus(juju)

    return _deploy_cluster(juju, all_workers, coordinator=coordinator, s3=s3)


def _get_query_url(
    tempo_host: str,
    service_name: str = "tracegen",
    tls: bool = True,
    nonce: Optional[str] = None,
):
    nonce_param = f"%20tracegen.nonce={nonce}" if nonce else ""
    url = f"{'https' if tls else 'http'}://{tempo_host}:3200/api/search?tags=service.name={service_name}{nonce_param}"
    return url


def query_traces_from_client_localhost(
    tempo_host: str,
    service_name: str = "tracegen",
    tls: bool = True,
    nonce: Optional[str] = None,
):
    """Query traces by running requests.get from the test host machine (outside the cluster)."""
    url = _get_query_url(
        tempo_host,
        service_name,
        tls,
        nonce,
    )
    req = requests.get(
        url,
        verify=False,
        timeout=5,
    )
    assert req.status_code == 200, req.reason
    traces = json.loads(req.text)["traces"]
    return traces


def query_traces_from_client_pod(
    tempo_host: str,
    service_name: str = "tracegen",
    tls: bool = True,
    nonce: Optional[str] = None,
    source_pod: Optional[str] = None,
    juju: Optional[Juju] = None,
):
    """Query traces by running curl from inside a pod (within the cluster)."""
    url = _get_query_url(
        tempo_host,
        service_name,
        tls,
        nonce,
    )
    logger.info("Running curl from pod %s to %s", source_pod, url)
    result = juju.exec(f"curl -s {url}", unit=source_pod)
    response_text = result.stdout
    logger.info("Pod response: %s", response_text)
    traces = json.loads(response_text)["traces"]
    return traces


@retry(stop=stop_after_attempt(20), wait=wait_fixed(20))
def query_traces_patiently_from_client_localhost(
    tempo_host: str,
    service_name: str = "tracegen",
    tls: bool = True,
    nonce: Optional[str] = None,
):
    """Query traces from localhost with retries until traces are found."""
    logger.info("polling %s for service %r traces...", tempo_host, service_name)
    traces = query_traces_from_client_localhost(
        tempo_host,
        service_name=service_name,
        tls=tls,
        nonce=nonce,
    )
    assert len(traces) > 0, "no traces found"
    return traces


@retry(stop=stop_after_attempt(20), wait=wait_fixed(20))
def query_traces_patiently_from_client_pod(
    tempo_host: str,
    service_name: str = "tracegen",
    tls: bool = True,
    nonce: Optional[str] = None,
    source_pod: Optional[str] = None,
    juju: Optional[Juju] = None,
):
    """Query traces from inside a pod with retries until traces are found."""
    logger.info("polling %s for service %r traces...", tempo_host, service_name)
    traces = query_traces_from_client_pod(
        tempo_host,
        service_name=service_name,
        tls=tls,
        nonce=nonce,
        source_pod=source_pod,
        juju=juju,
    )
    assert len(traces) > 0, "no traces found"
    return traces


def get_ingested_traces_service_names(tempo_host: str, tls: bool) -> Set[str]:
    """Fetch all ingested traces tags."""
    logger.info("querying %s for tags...", tempo_host)

    url = f"{'https' if tls else 'http'}://{tempo_host}:3200/api/search/tag/service.name/values"
    req = requests.get(
        url,
        verify=False,
    )
    assert req.status_code == 200, req.reason
    tags = cast(List[str], json.loads(req.text)["tagValues"])
    return set(tags)


def emit_trace(
    endpoint: str,
    juju: Juju,
    nonce: Optional[str] = None,
    proto: str = "otlp_http",
    service_name: Optional[str] = "tracegen",
    verbose: int = 0,
    use_cert: bool = False,
):
    """Use juju ssh to run tracegen from the tempo charm; to avoid any DNS issues."""
    logger.info("pushing tracegen onto %s/0", TEMPO_APP)
    juju.cli("scp", str(TRACEGEN_SCRIPT_PATH), f"{TEMPO_APP}/0:tracegen.py")
    juju.cli(
        "ssh",
        f"{TEMPO_APP}/0",
        f"apt update -y && "
        f"apt install git -y && "
        f"curl -LsSf https://astral.sh/uv/install.sh | sh",
    )

    tracegen_deps = (
        "protobuf==3.20.*",
        "opentelemetry-exporter-otlp-proto-http==1.21.0",
        "opentelemetry-exporter-otlp-proto-grpc",
        "opentelemetry-exporter-zipkin",
        "opentelemetry-exporter-jaeger",
        "protobuf==3.20.*",
        "thrift@git+https://github.com/apache/thrift.git@6e380306ef48af4050a61f2f91b3c8380d8e78fb#subdirectory=lib/py",
    )
    with_deps = " ".join(f"--with '{dep}'" for dep in tracegen_deps)
    cmd = (
        f"juju ssh -m {juju.model} {TEMPO_APP}/0 "
        f"TRACEGEN_SERVICE='{service_name or ''}' "
        f"TRACEGEN_ENDPOINT='{endpoint}' "
        f"TRACEGEN_VERBOSE='{verbose}' "
        f"TRACEGEN_PROTOCOL='{proto}' "
        f"TRACEGEN_CERT='{CA_CERT_PATH if use_cert else ''}' "
        f"TRACEGEN_NONCE='{nonce or ''}' "
        f"~/.local/bin/uv run {with_deps} tracegen.py"
    )

    logger.info("running tracegen with %r", cmd)

    lexed_cmd = shlex.split(cmd)
    out = subprocess.run(lexed_cmd, text=True, capture_output=True, check=True)
    logger.info("tracegen completed; stdout=%r", out.stdout)
    return out


def _get_endpoint(protocol: str, hostname: str, tls: bool):
    protocol_endpoint = protocols_endpoints.get(protocol) or api_endpoints.get(protocol)
    if protocol_endpoint is None:
        raise ValueError(f"Invalid protocol {protocol}")

    if "grpc" in protocol:
        return protocol_endpoint.format(hostname=hostname)
    return protocol_endpoint.format(
        hostname=hostname, scheme="https" if tls else "http"
    )


def get_tempo_ingressed_endpoint(hostname: str, protocol: str, tls: bool):
    return _get_endpoint(protocol, hostname, tls)


def get_tempo_internal_endpoint(juju: Juju, protocol: str, tls: bool, unit: int = 0):
    hostname = (
        f"{TEMPO_APP}-{unit}.{TEMPO_APP}-endpoints.{juju.model}.svc.cluster.local"
    )
    return _get_endpoint(protocol, hostname, tls)


def get_tempo_application_endpoint(tempo_ip: str, protocol: str, tls: bool):
    return _get_endpoint(protocol, tempo_ip, tls)


def get_ingress_proxied_hostname(juju: Juju):
    return json.loads(
        juju.run(TRAEFIK_APP + "/0", "show-proxied-endpoints").results[
            "proxied-endpoints"
        ]
    )[TRAEFIK_APP]["url"].split("://")[1]


def get_istio_ingress_ip(juju: Juju, app_name: str = "istio-ingress"):
    """Get the istio-ingress public IP address from Kubernetes."""
    gateway_resource = create_namespaced_resource(
        group="gateway.networking.k8s.io",
        version="v1",
        kind="Gateway",
        plural="gateways",
    )
    client = Client()
    gateway = client.get(gateway_resource, app_name, namespace=juju.model)
    if gateway.status and gateway.status.get("addresses"):
        return gateway.status["addresses"][0]["value"]
    raise ValueError(f"No ingress address found for {app_name}")


@contextmanager
def service_mesh(
    juju: Juju,
    beacon_app_name: str,
    apps_to_be_related_with_beacon: List[str],
):
    """Temporarily enable service mesh in the model."""
    juju.config(ISTIO_BEACON_APP, {"model-on-mesh": "true"})

    for app in apps_to_be_related_with_beacon:
        juju.integrate(beacon_app_name + ":service-mesh", app + ":service-mesh")
    juju.wait(
        all_active,
        timeout=1000,
        delay=5,
        successes=5,
    )

    yield

    juju.config(ISTIO_BEACON_APP, {"model-on-mesh": "false"})

    for app in apps_to_be_related_with_beacon:
        juju.remove_relation(
            beacon_app_name + ":service-mesh", app + ":service-mesh", force=True
        )
    juju.wait(
        all_active,
        timeout=1000,
        delay=5,
        successes=5,
    )
