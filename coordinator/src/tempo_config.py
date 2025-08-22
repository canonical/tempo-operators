# Copyright 2023 Canonical
# See LICENSE file for licensing details.

"""Helper module for interacting with the Tempo configuration."""

import enum
import logging
from enum import Enum, unique
from pathlib import Path
from typing import Any, Dict, List, Optional

import pydantic
from coordinated_workers.coordinator import ClusterRolesConfig
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# TODO: inherit enum.StrEnum when jammy is no longer supported.
# https://docs.python.org/3/library/enum.html#enum.StrEnum
@unique
class TempoRole(str, Enum):
    """Tempo component role names.

    References:
     arch:
      -> https://grafana.com/docs/tempo/latest/operations/architecture/
     config:
      -> https://grafana.com/docs/tempo/latest/configuration/#server
    """

    # scalable-single-binary is a bit too long to type
    all = "all"  # default, meta-role. gets remapped to scalable-single-binary by the worker.

    querier = "querier"
    query_frontend = "query-frontend"
    ingester = "ingester"
    distributor = "distributor"
    compactor = "compactor"
    metrics_generator = "metrics-generator"

    @staticmethod
    def all_nonmeta():
        return {
            TempoRole.querier,
            TempoRole.query_frontend,
            TempoRole.ingester,
            TempoRole.distributor,
            TempoRole.compactor,
            TempoRole.metrics_generator,
        }


META_ROLES = {
    "all": set(TempoRole.all_nonmeta()),
}
# Tempo component meta-role names.

MINIMAL_DEPLOYMENT = {
    TempoRole.querier: 1,
    TempoRole.query_frontend: 1,
    TempoRole.ingester: 1,
    TempoRole.distributor: 1,
    TempoRole.compactor: 1,
}
# The minimal set of roles that need to be allocated for the
# deployment to be considered consistent (otherwise we set blocked).

RECOMMENDED_DEPLOYMENT = {
    TempoRole.querier.value: 1,
    TempoRole.query_frontend.value: 1,
    TempoRole.ingester.value: 3,
    TempoRole.distributor.value: 1,
    TempoRole.compactor.value: 1,
    TempoRole.metrics_generator.value: 1,
}
# The set of roles that need to be allocated for the
# deployment to be considered robust according to Grafana Tempo's
# Helm chart configurations.
# https://github.com/grafana/helm-charts/blob/main/charts/tempo-distributed/


TEMPO_ROLES_CONFIG = ClusterRolesConfig(
    roles={role for role in TempoRole},
    meta_roles=META_ROLES,
    minimal_deployment=MINIMAL_DEPLOYMENT,
    recommended_deployment=RECOMMENDED_DEPLOYMENT,
)
# Define the configuration for Tempo roles.


class ClientAuthTypeEnum(str, enum.Enum):
    """Client auth types."""

    # Possible values https://pkg.go.dev/crypto/tls#ClientAuthType
    VERIFY_CLIENT_CERT_IF_GIVEN = "VerifyClientCertIfGiven"
    NO_CLIENT_CERT = "NoClientCert"
    REQUEST_CLIENT_CERT = "RequestClientCert"
    REQUIRE_ANY_CLIENT_CERT = "RequireAnyClientCert"
    REQUIRE_AND_VERIFY_CLIENT_CERT = "RequireAndVerifyClientCert"


class MetricsGeneratorProcessorLabel(str, enum.Enum):
    """Metrics generator processors supported values.

    Supported values: https://grafana.com/docs/tempo/latest/configuration/#standard-overrides
    """

    SERVICE_GRAPHS = "service-graphs"
    SPAN_METRICS = "span-metrics"
    LOCAL_BLOCKS = "local-blocks"


class InvalidConfigurationError(Exception):
    """Invalid configuration."""

    pass


class Kvstore(BaseModel):
    """Kvstore schema."""

    store: str = "memberlist"


class Ring(BaseModel):
    """Ring schema."""

    kvstore: Kvstore


class IngesterRing(BaseModel):
    """Ingester ring schema."""

    kvstore: Kvstore
    replication_factor: int


class Lifecycler(BaseModel):
    """Lifecycler schema."""

    ring: IngesterRing


class Memberlist(BaseModel):
    """Memberlist schema."""

    abort_if_cluster_join_fails: bool
    bind_port: int
    join_members: List[str]
    tls_enabled: bool = False
    tls_cert_path: Optional[str] = None
    tls_key_path: Optional[str] = None
    tls_ca_path: Optional[str] = None
    tls_server_name: Optional[str] = None


class ClientTLS(BaseModel):
    """Client tls config schema."""

    tls_enabled: bool
    tls_cert_path: str
    tls_key_path: str
    tls_ca_path: str
    tls_server_name: str


class Client(BaseModel):
    """Client schema."""

    grpc_client_config: ClientTLS


class Distributor(BaseModel):
    """Distributor schema."""

    ring: Ring
    receivers: Dict[str, Any]


class Ingester(BaseModel):
    """Ingester schema."""

    trace_idle_period: str
    max_block_bytes: int
    max_block_duration: str
    lifecycler: Lifecycler


class FrontendWorker(BaseModel):
    """FrontendWorker schema."""

    frontend_address: str
    grpc_client_config: Optional[ClientTLS] = None


class Querier(BaseModel):
    """Querier schema."""

    frontend_worker: FrontendWorker


class TLS(BaseModel):
    """TLS configuration schema."""

    model_config = ConfigDict(
        # Allow serializing enum values.
        use_enum_values=True
    )
    """Pydantic config."""

    cert_file: str
    key_file: str
    client_ca_file: str
    client_auth_type: ClientAuthTypeEnum = (
        ClientAuthTypeEnum.VERIFY_CLIENT_CERT_IF_GIVEN
    )


class Server(BaseModel):
    """Server schema."""

    http_listen_port: int
    grpc_listen_port: int
    http_tls_config: Optional[TLS] = None
    grpc_tls_config: Optional[TLS] = None


class Compaction(BaseModel):
    """Compaction schema."""

    compaction_window: str
    max_compaction_objects: int
    block_retention: str
    compacted_block_retention: str


class Compactor(BaseModel):
    """Compactor schema."""

    ring: Ring
    compaction: Compaction


class Pool(BaseModel):
    """Pool schema."""

    max_workers: int
    queue_depth: int


class Wal(BaseModel):
    """Wal schema."""

    path: Path


class S3(BaseModel):
    """S3 config schema."""

    model_config = ConfigDict(populate_by_name=True)
    """Pydantic config."""

    # Use aliases to override keys in `coordinator::_s3_config`
    # to align with upstream Tempo's configuration keys: `bucket`, `access_key`, `secret_key`.
    bucket_name: str = Field(alias="bucket")
    access_key_id: str = Field(alias="access_key")
    endpoint: str
    region: Optional[str] = None
    secret_access_key: str = Field(alias="secret_key")
    insecure: bool = False
    tls_ca_path: Optional[str] = None


class Block(BaseModel):
    """Block schema."""

    version: Optional[str]


class TraceStorage(BaseModel):
    """Trace Storage schema."""

    wal: Wal
    pool: Pool
    backend: str
    s3: S3
    block: Block


class Storage(BaseModel):
    """Storage schema."""

    trace: TraceStorage


class RemoteWriteTLS(BaseModel):
    """Remote write TLS schema."""

    ca_file: str
    insecure_skip_verify: bool = False


class RemoteWrite(BaseModel):
    """Remote write schema."""

    url: str
    tls_config: Optional[RemoteWriteTLS] = None


class MetricsGeneratorSpanMetricsProcessor(BaseModel):
    """Metrics Generator span_metrics processor configuration schema."""

    # see https://grafana.com/docs/tempo/v2.6.x/configuration/#metrics-generator
    # for a full list of config options


class MetricsGeneratorServiceGraphsProcessor(BaseModel):
    """Metrics Generator service_graphs processor configuration schema."""

    # see https://grafana.com/docs/tempo/v2.6.x/configuration/#metrics-generator
    # for a full list of config options


class MetricsGeneratorProcessor(BaseModel):
    """Metrics Generator processor schema."""

    span_metrics: MetricsGeneratorSpanMetricsProcessor
    service_graphs: MetricsGeneratorServiceGraphsProcessor
    # see https://grafana.com/docs/tempo/v2.6.x/configuration/#metrics-generator
    # for a full list of config options; could add local_blocks here


class MetricsGeneratorStorage(BaseModel):
    """Metrics Generator storage schema."""

    path: str
    remote_write: List[RemoteWrite]


class MetricsGenerator(BaseModel):
    """Metrics Generator schema."""

    ring: Ring
    storage: MetricsGeneratorStorage

    # processor-specific config depends on the processor type
    processor: MetricsGeneratorProcessor


class MetricsGeneratorDefaults(BaseModel):
    """Metrics generator defaults schema."""

    model_config = ConfigDict(
        # Allow serializing enum values.
        use_enum_values=True
    )
    """Pydantic config."""
    processors: Optional[List[MetricsGeneratorProcessorLabel]] = pydantic.Field(
        default_factory=list
    )


class Defaults(BaseModel):
    """Defaults schema."""

    metrics_generator: MetricsGeneratorDefaults


class Overrides(BaseModel):
    """Overrides schema."""

    defaults: Defaults


class TempoConfig(BaseModel):
    """Tempo config schema."""

    auth_enabled: bool
    server: Server
    distributor: Distributor
    ingester: Ingester
    memberlist: Memberlist
    compactor: Compactor
    querier: Querier
    storage: Storage
    ingester_client: Optional[Client] = None
    metrics_generator_client: Optional[Client] = None
    metrics_generator: Optional[MetricsGenerator] = None
    overrides: Optional[Overrides] = None
