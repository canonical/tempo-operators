import os
import time
from pathlib import Path
from typing import Any, Literal, get_args
import requests

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter as GRPCExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter as HTTPExporter
from opentelemetry.exporter.zipkin.json import ZipkinExporter
from opentelemetry.exporter.jaeger.thrift import JaegerExporter as JaegerThriftHttpExporter
from opentelemetry.exporter.jaeger.proto.grpc import JaegerExporter as JaegerGRPCExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
)

ReceiverProtocol = Literal[
    "zipkin",
    "otlp_grpc",
    "otlp_http",
    "jaeger_grpc",
    "jaeger_thrift_http",
]

def set_envvars(cert: Path = None):
    ca_cert_path = str(Path(cert).absolute()) if cert else ""
    os.environ['OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE'] = ca_cert_path
    os.environ['OTEL_EXPORTER_JAEGER_CERTIFICATE'] = ca_cert_path
    # jaeger thrift http exporter does not expose a parameter to set path for CA verification
    os.environ['SSL_CERT_FILE'] = ca_cert_path
    os.environ["REQUESTS_CA_BUNDLE"] = ca_cert_path

def initialize_exporter(protocol: str, endpoint: str, cert: Path = None):
    # ip:4317
    if protocol == "otlp_grpc":
        return GRPCExporter(
            endpoint=endpoint,
            insecure=not cert,
        )
    # scheme://ip:4318/v1/traces
    elif protocol == "otlp_http":
        return HTTPExporter(
            endpoint=endpoint,
        )
    # scheme://ip:9411/v1/traces
    elif protocol == "zipkin":
        # zipkin does not expose an arg to pass certificate
        session = requests.Session() 
        if cert:
            session.verify = cert
        return ZipkinExporter(
            endpoint=endpoint,
            session=session,
        )
    # scheme://ip:14268/api/traces?format=jaeger.thrift
    elif protocol == "jaeger_thrift_http":
        return JaegerThriftHttpExporter(
            collector_endpoint=endpoint,
        )
    # ip:14250
    elif protocol == "jaeger_grpc":
        return JaegerGRPCExporter(
            collector_endpoint = endpoint,
            insecure=not cert,
        )
    else:
        raise ValueError(f"Unsupported protocol: {protocol}")

def emit_trace(
        endpoint: str,
        log_trace_to_console: bool = False,
        cert: Path = None,
        protocol: ReceiverProtocol = "otlp_http",
        nonce: Any = None
):
    if protocol == "ALL":
        for proto in get_args(protocol):
            emit_trace(endpoint, log_trace_to_console, cert, proto, nonce=nonce)
    else:
        set_envvars(cert)
        span_exporter = initialize_exporter(protocol, endpoint, cert)
        return _export_trace(span_exporter, log_trace_to_console=log_trace_to_console, nonce=nonce, protocol = protocol)

    

        
        


def _export_trace(span_exporter, log_trace_to_console: bool = False, nonce: Any = None, protocol: ReceiverProtocol = "otlp_http"):
    resource = Resource.create(attributes={
        "service.name": f"tracegen-{protocol}",
        "nonce": str(nonce)
    }
    )
    provider = TracerProvider(resource=resource)

    if log_trace_to_console:
        processor = BatchSpanProcessor(ConsoleSpanExporter())
        provider.add_span_processor(processor)

    span_processor = BatchSpanProcessor(span_exporter)
    provider.add_span_processor(span_processor)
    trace.set_tracer_provider(provider)

    tracer = trace.get_tracer(__name__)

    with tracer.start_as_current_span("foo"):
        with tracer.start_as_current_span("bar"):
            with tracer.start_as_current_span("baz"):
                time.sleep(.1)

    return span_exporter.force_flush()


if __name__ == '__main__':
    emit_trace(
        endpoint=os.getenv("TRACEGEN_ENDPOINT", "http://127.0.0.1:8080"),
        cert=os.getenv("TRACEGEN_CERT", None),
        log_trace_to_console=os.getenv("TRACEGEN_VERBOSE", False),
        protocol=os.getenv("TRACEGEN_PROTOCOL", "http"),
        nonce=os.getenv("TRACEGEN_NONCE", "24")
    )
