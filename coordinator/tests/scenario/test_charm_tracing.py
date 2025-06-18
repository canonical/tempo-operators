import functools
import logging
import os
import socket
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from charms.tempo_coordinator_k8s.v0.charm_tracing import (
    BUFFER_DEFAULT_CACHE_FILE_NAME,
    CHARM_TRACING_ENABLED,
)
from charms.tempo_coordinator_k8s.v0.charm_tracing import (
    _autoinstrument as autoinstrument,
)
from charms.tempo_coordinator_k8s.v0.charm_tracing import (
    _Buffer,
    get_current_span,
    trace,
    trace_charm,
)
from charms.tempo_coordinator_k8s.v0.tracing import (
    ProtocolType,
    Receiver,
    TracingEndpointRequirer,
    TracingProviderAppData,
    TracingRequirerAppData,
    charm_tracing_config,
)
from ops import EventBase, EventSource, Framework
from ops.charm import CharmBase, CharmEvents
from ops.testing import Context, Relation, State

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def cleanup():
    # if any other test module disabled it...
    os.environ[CHARM_TRACING_ENABLED] = "1"

    def patched_set_tracer_provider(tracer_provider, log):
        import opentelemetry

        opentelemetry.trace._TRACER_PROVIDER = tracer_provider

    with patch("opentelemetry.trace._set_tracer_provider", new=patched_set_tracer_provider):
        yield


class MyCharmSimple(CharmBase):
    META = {"name": "frank"}

    @property
    def tempo(self):
        return "foo.bar:80"


autoinstrument(MyCharmSimple, "tempo", buffer_max_events=0)


def test_base_tracer_endpoint(caplog):
    import opentelemetry

    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"
    ) as f:
        f.return_value = opentelemetry.sdk.trace.export.SpanExportResult.SUCCESS
        ctx = Context(MyCharmSimple, meta=MyCharmSimple.META)
        ctx.run(ctx.on.start(), State())
        # assert "Setting up span exporter to endpoint: foo.bar:80" in caplog.text
        assert "Starting root trace with id=" in caplog.text
        span = f.call_args_list[0].args[0][0]
        assert span.resource.attributes["service.name"] == "frank-charm"
        assert span.resource.attributes["compose_service"] == "frank-charm"
        assert span.resource.attributes["charm_type"] == "MyCharmSimple"


class SubObject:
    def foo(self):
        return "bar"


class MyCharmSubObject(CharmBase):
    META = {"name": "frank"}

    def __init__(self, framework: Framework):
        super().__init__(framework)
        self.subobj = SubObject()
        framework.observe(self.on.start, self._on_start)

    def _on_start(self, _):
        self.subobj.foo()

    @property
    def tempo(self):
        return "foo.bar:80"


autoinstrument(MyCharmSubObject, "tempo", extra_types=[SubObject], buffer_max_events=0)


def test_subobj_tracer_endpoint(caplog):
    import opentelemetry

    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"
    ) as f:
        f.return_value = opentelemetry.sdk.trace.export.SpanExportResult.SUCCESS
        ctx = Context(MyCharmSubObject, meta=MyCharmSubObject.META)
        ctx.run(ctx.on.start(), State())
        spans = f.call_args_list[0].args[0]
        assert f.call_count == 1
        assert spans[0].name == "method call: SubObject.foo"


class MyCharmInitAttr(CharmBase):
    META = {"name": "frank"}

    def __init__(self, framework: Framework):
        super().__init__(framework)
        self._tempo = "foo.bar:80"

    @property
    def tempo(self):
        return self._tempo


autoinstrument(MyCharmInitAttr, "tempo", buffer_max_events=0)


def test_init_attr(caplog):
    import opentelemetry

    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"
    ) as f:
        f.return_value = opentelemetry.sdk.trace.export.SpanExportResult.SUCCESS
        ctx = Context(MyCharmInitAttr, meta=MyCharmInitAttr.META)
        ctx.run(ctx.on.start(), State())
        # assert "Setting up span exporter to endpoint: foo.bar:80" in caplog.text
        span = f.call_args_list[0].args[0][0]
        assert span.resource.attributes["service.name"] == "frank-charm"
        assert span.resource.attributes["compose_service"] == "frank-charm"
        assert span.resource.attributes["charm_type"] == "MyCharmInitAttr"


class MyCharmSimpleDisabled(CharmBase):
    META = {"name": "frank"}

    @property
    def tempo(self):
        return None


autoinstrument(MyCharmSimpleDisabled, "tempo", buffer_max_events=0)


def test_base_tracer_endpoint_disabled(caplog):
    import opentelemetry

    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"
    ) as f:
        f.return_value = opentelemetry.sdk.trace.export.SpanExportResult.SUCCESS
        ctx = Context(MyCharmSimpleDisabled, meta=MyCharmSimpleDisabled.META)
        ctx.run(ctx.on.start(), State())

        assert not f.called


@trace
def _my_fn(foo):
    return foo + 1


class MyCharmSimpleEvent(CharmBase):
    META = {"name": "frank"}

    def __init__(self, fw):
        super().__init__(fw)
        span = get_current_span()
        assert span is None  # can't do that in init.
        fw.observe(self.on.start, self._on_start)

    def _on_start(self, _):
        span = get_current_span()
        span.add_event(
            "log",
            {
                "foo": "bar",
                "baz": "qux",
            },
        )
        _my_fn(2)

    @property
    def tempo(self):
        return "foo.bar:80"


autoinstrument(MyCharmSimpleEvent, "tempo", buffer_max_events=0)


def test_base_tracer_endpoint_event(caplog):
    import opentelemetry

    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"
    ) as f:
        f.return_value = opentelemetry.sdk.trace.export.SpanExportResult.SUCCESS
        ctx = Context(MyCharmSimpleEvent, meta=MyCharmSimpleEvent.META)
        ctx.run(ctx.on.start(), State())

        spans = f.call_args_list[0].args[0]
        span0, span1, span2, span3 = spans
        assert span0.name == "function call: _my_fn"

        assert span1.name == "method call: MyCharmSimpleEvent._on_start"

        assert span2.name == "event: start"
        evt = span2.events[0]
        assert evt.name == "start"

        assert span3.name == "frank/0: start event"

        for span in spans:
            assert span.resource.attributes["service.name"] == "frank-charm"


def test_juju_topology_injection(caplog):
    import opentelemetry

    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"
    ) as f:
        f.return_value = opentelemetry.sdk.trace.export.SpanExportResult.SUCCESS
        ctx = Context(MyCharmSimpleEvent, meta=MyCharmSimpleEvent.META)
        state = ctx.run(ctx.on.start(), State())

        spans = f.call_args_list[0].args[0]

        for span in spans:
            # topology
            assert span.resource.attributes["juju_unit"] == "frank/0"
            assert span.resource.attributes["juju_application"] == "frank"
            assert span.resource.attributes["juju_model"] == state.model.name
            assert span.resource.attributes["juju_model_uuid"] == state.model.uuid


class MyCharmWithMethods(CharmBase):
    META = {"name": "frank"}

    def __init__(self, fw):
        super().__init__(fw)
        fw.observe(self.on.start, self._on_start)

    def _on_start(self, _):
        self.a()
        self.b()
        self.c()

    def a(self):
        pass

    def b(self):
        pass

    def c(self):
        pass

    @property
    def tempo(self):
        return "foo.bar:80"


autoinstrument(MyCharmWithMethods, "tempo", buffer_max_events=0)


def test_base_tracer_endpoint_methods(caplog):
    import opentelemetry

    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"
    ) as f:
        f.return_value = opentelemetry.sdk.trace.export.SpanExportResult.SUCCESS
        ctx = Context(MyCharmWithMethods, meta=MyCharmWithMethods.META)
        ctx.run(ctx.on.start(), State())

        spans = f.call_args_list[0].args[0]
        span_names = [span.name for span in spans]
        assert span_names == [
            "method call: MyCharmWithMethods.a",
            "method call: MyCharmWithMethods.b",
            "method call: MyCharmWithMethods.c",
            "method call: MyCharmWithMethods._on_start",
            "event: start",
            "frank/0: start event",
        ]


class Foo(EventBase):
    pass


class MyEvents(CharmEvents):
    foo = EventSource(Foo)


class MyCharmWithCustomEvents(CharmBase):
    on = MyEvents()

    META = {"name": "frank"}

    def __init__(self, fw):
        super().__init__(fw)
        fw.observe(self.on.start, self._on_start)
        fw.observe(self.on.foo, self._on_foo)

    def _on_start(self, _):
        self.on.foo.emit()

    def _on_foo(self, _):
        pass

    @property
    def tempo(self):
        return "foo.bar:80"


autoinstrument(MyCharmWithCustomEvents, "tempo", buffer_max_events=0)


def test_base_tracer_endpoint_custom_event(caplog):
    import opentelemetry

    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"
    ) as f:
        f.return_value = opentelemetry.sdk.trace.export.SpanExportResult.SUCCESS
        ctx = Context(MyCharmWithCustomEvents, meta=MyCharmWithCustomEvents.META)
        ctx.run(ctx.on.start(), State())

        spans = f.call_args_list[0].args[0]
        span_names = [span.name for span in spans]
        assert span_names == [
            "method call: MyCharmWithCustomEvents._on_foo",
            "event: foo",
            "method call: MyCharmWithCustomEvents._on_start",
            "event: start",
            "frank/0: start event",
        ]
        # only the charm exec span is a root
        assert not spans[-1].parent
        for span in spans[:-1]:
            assert span.parent
            assert span.parent.trace_id
        assert len({(span.parent.trace_id if span.parent else 0) for span in spans}) == 2


class MyRemoteCharm(CharmBase):
    META = {"name": "charlie", "requires": {"tracing": {"interface": "tracing", "limit": 1}}}
    _request = True

    def __init__(self, framework: Framework):
        super().__init__(framework)
        self.tracing = TracingEndpointRequirer(
            self, "tracing", protocols=(["otlp_http"] if self._request else [])
        )

    def tempo(self):
        return self.tracing.get_endpoint("otlp_http")


autoinstrument(MyRemoteCharm, "tempo", buffer_max_events=0)


@pytest.mark.parametrize("leader", (True, False))
def test_tracing_requirer_remote_charm_request_response(leader):
    # IF the leader unit (whoever it is) did request the endpoint to be activated
    MyRemoteCharm._request = True
    ctx = Context(MyRemoteCharm, meta=MyRemoteCharm.META)
    # WHEN you get any event AND the remote unit has already replied
    tracing = Relation(
        "tracing",
        # if we're not leader, assume the leader did its part already
        local_app_data=(
            TracingRequirerAppData(receivers=["otlp_http"]).dump() if not leader else {}
        ),
        remote_app_data=TracingProviderAppData(
            host="foo.com",
            receivers=[
                Receiver(
                    url="http://foo.com:80", protocol=ProtocolType(name="otlp_http", type="http")
                )
            ],
        ).dump(),
    )
    with ctx(ctx.on.start(), State(leader=leader, relations=[tracing])) as mgr:
        # THEN you're good
        assert mgr.charm.tempo() == "http://foo.com:80"


@pytest.mark.parametrize("leader", (True, False))
def test_tracing_requirer_remote_charm_no_request_but_response(leader):
    # IF the leader did NOT request the endpoint to be activated
    MyRemoteCharm._request = False
    ctx = Context(MyRemoteCharm, meta=MyRemoteCharm.META)
    # WHEN you get any event AND the remote unit has already replied
    tracing = Relation(
        "tracing",
        # empty local app data
        remote_app_data=TracingProviderAppData(
            # but the remote end has sent the data you need
            receivers=[
                Receiver(
                    url="http://foo.com:80", protocol=ProtocolType(name="otlp_http", type="http")
                )
            ],
        ).dump(),
    )
    with ctx(ctx.on.start(), State(leader=leader, relations=[tracing])) as mgr:
        # THEN you're lucky, but you're good
        assert mgr.charm.tempo() == "http://foo.com:80"


@pytest.mark.parametrize("relation", (True, False))
@pytest.mark.parametrize("leader", (True, False))
def test_tracing_requirer_remote_charm_no_request_no_response(leader, relation):
    """Verify that the charm errors out (even with charm_tracing disabled) if the tempo() call raises."""
    # IF the leader did NOT request the endpoint to be activated
    MyRemoteCharm._request = False
    ctx = Context(MyRemoteCharm, meta=MyRemoteCharm.META)
    # WHEN you get any event
    if relation:
        # AND you have an empty relation
        tracing = Relation(
            "tracing",
            # empty local and remote app data
        )
        relations = [tracing]
    else:
        # OR no relation at all
        relations = []

    # THEN self.tempo() will raise on init
    # FIXME: non-leader units should get a permission denied exception,
    # but it won't fire due to https://github.com/canonical/operator/issues/1378
    with pytest.raises(Exception, match=r"ProtocolNotRequestedError"):
        ctx.run(ctx.on.start(), State(relations=relations, leader=leader))


class MyRemoteBorkyCharm(CharmBase):
    META = {"name": "charlie", "requires": {"tracing": {"interface": "tracing", "limit": 1}}}
    _borky_return_value = None

    def tempo(self):
        return self._borky_return_value


autoinstrument(MyRemoteBorkyCharm, "tempo", buffer_max_events=0)


@pytest.mark.parametrize("borky_return_value", (True, 42, object(), 0.2, [], (), {}))
def test_borky_tempo_return_value(borky_return_value, caplog):
    """Verify that the charm exits 1 (even with charm_tracing disabled) if the tempo() call returns bad values."""
    # IF the charm's tempo endpoint getter returns anything but None or str
    MyRemoteBorkyCharm._borky_return_value = borky_return_value
    ctx = Context(MyRemoteBorkyCharm, meta=MyRemoteBorkyCharm.META)
    # WHEN you get any event
    # THEN the self.tempo getter will raise and charm exec will exit 1

    # traceback from the TypeError raised by _get_tracing_endpoint
    with pytest.raises(
        Exception,
        match=r"MyRemoteBorkyCharm\.tempo should resolve to a tempo "
        r"endpoint \(string\); got (.*) instead\.",
    ):
        ctx.run(ctx.on.start(), State())


class MyCharmStaticMethods(CharmBase):
    META = {"name": "jolene"}

    def __init__(self, fw):
        super().__init__(fw)
        fw.observe(self.on.start, self._on_start)
        fw.observe(self.on.update_status, self._on_update_status)

    def _on_start(self, _):
        for o in (OtherObj(), OtherObj):
            for meth in ("_staticmeth", "_staticmeth1", "_staticmeth2"):
                assert getattr(o, meth)(1) == 2

    def _on_update_status(self, _):
        # super-ugly edge cases
        OtherObj()._staticmeth3(OtherObj())
        OtherObj()._staticmeth4(OtherObj())
        OtherObj._staticmeth3(OtherObj())
        OtherObj._staticmeth4(OtherObj(), foo=2)

    @property
    def tempo(self):
        return "foo.bar:80"


class OtherObj:
    @staticmethod
    def _staticmeth(i: int, *args, **kwargs):
        return 1 + i

    @staticmethod
    def _staticmeth1(i: int):
        return 1 + i

    @staticmethod
    def _staticmeth2(i: int, foo="bar"):
        return 1 + i

    @staticmethod
    def _staticmeth3(abc: "OtherObj", foo="bar"):
        return 1 + 1

    @staticmethod
    def _staticmeth4(abc: int, foo="bar"):
        return 1 + 1


autoinstrument(MyCharmStaticMethods, "tempo", extra_types=[OtherObj], buffer_max_events=0)


def test_trace_staticmethods(caplog):
    import opentelemetry

    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"
    ) as f:
        f.return_value = opentelemetry.sdk.trace.export.SpanExportResult.SUCCESS
        ctx = Context(MyCharmStaticMethods, meta=MyCharmStaticMethods.META)
        ctx.run(ctx.on.start(), State())

        spans = f.call_args_list[0].args[0]

        span_names = [span.name for span in spans]
        assert span_names == [
            "method call: OtherObj._staticmeth",
            "method call: OtherObj._staticmeth1",
            "method call: OtherObj._staticmeth2",
            "method call: OtherObj._staticmeth",
            "method call: OtherObj._staticmeth1",
            "method call: OtherObj._staticmeth2",
            "method call: MyCharmStaticMethods._on_start",
            "event: start",
            "jolene/0: start event",
        ]

        for span in spans:
            assert span.resource.attributes["service.name"] == "jolene-charm"


def test_trace_staticmethods_bork(caplog):
    import opentelemetry

    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"
    ) as f:
        f.return_value = opentelemetry.sdk.trace.export.SpanExportResult.SUCCESS
        ctx = Context(MyCharmStaticMethods, meta=MyCharmStaticMethods.META)
        ctx.run(ctx.on.update_status(), State())


class SuperCharm(CharmBase):
    def foo(self):
        return "bar"


class MyInheritedCharm(SuperCharm):
    META = {"name": "godcat"}

    def __init__(self, framework: Framework):
        super().__init__(framework)
        framework.observe(self.on.start, self._on_start)

    def _on_start(self, _):
        self.foo()

    @property
    def tempo(self):
        return "foo.bar:80"


autoinstrument(MyInheritedCharm, "tempo", buffer_max_events=0)


def test_inheritance_tracing(caplog):
    import opentelemetry

    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"
    ) as f:
        f.return_value = opentelemetry.sdk.trace.export.SpanExportResult.SUCCESS
        ctx = Context(MyInheritedCharm, meta=MyInheritedCharm.META)
        ctx.run(ctx.on.start(), State())
        spans = f.call_args_list[0].args[0]
        assert spans[0].name == "method call: SuperCharm.foo"


def bad_wrapper(func):
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper


def good_wrapper(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper


class MyCharmWrappedMethods(CharmBase):
    META = {"name": "catgod"}

    def __init__(self, fw):
        super().__init__(fw)
        fw.observe(self.on.start, self._on_start)

    @good_wrapper
    def a(self):
        pass

    @bad_wrapper
    def b(self):
        pass

    def _on_start(self, _):
        self.a()
        self.b()

    @property
    def tempo(self):
        return "foo.bar:80"


autoinstrument(MyCharmWrappedMethods, "tempo", buffer_max_events=0)


def test_wrapped_method_wrapping(caplog):
    import opentelemetry

    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter.export"
    ) as f:
        f.return_value = opentelemetry.sdk.trace.export.SpanExportResult.SUCCESS
        ctx = Context(MyCharmWrappedMethods, meta=MyCharmWrappedMethods.META)
        ctx.run(ctx.on.start(), State())
        spans = f.call_args_list[0].args[0]
        assert spans[0].name == "method call: MyCharmWrappedMethods.a"
        assert spans[1].name == "method call: @bad_wrapper(MyCharmWrappedMethods.b)"


def make_buffering_charm(
    crt_path: Optional[Path],
    buffer_path: Path = None,
    buffer_max_events: int = 100,
    buffer_max_size: int = 100,
):
    @trace_charm(
        tracing_endpoint="tracing_endpoint",
        server_cert="server_cert",
        **({"buffer_path": buffer_path} if buffer_path else {}),
        buffer_max_events=buffer_max_events,
        buffer_max_size_mib=buffer_max_size,
    )
    class MyBufferingCharm(CharmBase):
        META = {"name": "josianne", "requires": {"tracing": {"interface": "tracing", "limit": 1}}}

        def __init__(self, framework: Framework):
            super().__init__(framework)
            self.tracing = TracingEndpointRequirer(self, "tracing")
            self.tracing_endpoint, self.server_cert = charm_tracing_config(self.tracing, crt_path)
            framework.observe(self.on.start, self._on_start)

        def _on_start(self, _):
            pass

    return MyBufferingCharm


@pytest.mark.parametrize("tls", (True, False))
def test_buffering_save(tmp_path, tls):
    if tls:
        cert = tmp_path / "mycert"
        cert.write_text("foo")
    else:
        cert = None

    buffer_path = tmp_path / "mycert"
    charm = make_buffering_charm(cert, buffer_path)

    # given a charm without a tracing relation
    ctx = Context(charm, meta=charm.META)
    # when we receive an event
    ctx.run(ctx.on.start(), State())

    # then the trace gets buffered
    buffer = _Buffer(buffer_path, 100, 100)
    assert buffer.load()


@pytest.mark.parametrize("tls", (True, False))
def test_buffering_flush(tmp_path, tls):
    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter._export"
    ) as f:
        mockresp = MagicMock()
        mockresp.status_code = 200
        f.return_value = mockresp

        if tls:
            cert = tmp_path / "mycert"
            cert.write_text("foo")
        else:
            cert = None

        buffer_path = tmp_path / BUFFER_DEFAULT_CACHE_FILE_NAME
        buffer_path.write_bytes(b"mockspan")

        charm = make_buffering_charm(cert, buffer_path)

        # given a charm with a tracing relation and a nonempty buffer
        ctx = Context(charm, meta=charm.META)
        # when we receive an event

        host = socket.getfqdn()
        tracing = Relation(
            "tracing",
            remote_app_data={
                "receivers": f'[{{"protocol": {{"name": "otlp_grpc", "type": "grpc"}}, "url": "{host}:4317"}}, '
                f'{{"protocol": {{"name": "otlp_http", "type": "http"}}, "url": "http://{host}:4318"}}, '
                f'{{"protocol": {{"name": "zipkin", "type": "http"}}, "url": "http://{host}:9411" }}]',
            },
        )

        ctx.run(ctx.on.start(), State(relations={tracing}))
        # then the buffered traces get flushed
        assert f.call_count == 3

        # and the buffer is empty
        assert buffer_path.read_bytes() == b""


@pytest.mark.parametrize("tls", (True, False))
def test_buffering_size_limit(tmp_path, tls):
    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter._export"
    ) as f:
        mockresp = MagicMock()
        mockresp.status_code = 200
        f.return_value = mockresp

        if tls:
            cert = tmp_path / "mycert"
            cert.write_text("foo")
        else:
            cert = None

        buffer_path = tmp_path / BUFFER_DEFAULT_CACHE_FILE_NAME

        # current buffer contains a span that's ~80mb large
        buffer_path.write_bytes(b"mockspan" * 10**7)
        # set max buffer size to 1mb
        charm = make_buffering_charm(cert, buffer_path, buffer_max_size=1)

        # given a charm with a tracing relation and a nonempty buffer
        ctx = Context(charm, meta=charm.META)
        # when we receive an event

        ctx.run(ctx.on.start(), State())
        # then the buffer only contains one span, and not the large one it had before
        buffer = _Buffer(buffer_path, 100, 100)
        buf = buffer.load()
        assert len(buf) == 1
        assert b"mockspan" not in buf[0]


@pytest.mark.parametrize("tls", (True, False))
@pytest.mark.parametrize("n_events", (5, 10))
def test_buffering_event_n_limit(tmp_path, tls, n_events):
    with patch(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter._export"
    ) as f:
        mockresp = MagicMock()
        mockresp.status_code = 200
        f.return_value = mockresp

        if tls:
            cert = tmp_path / "mycert"
            cert.write_text("foo")
        else:
            cert = None

        buffer_path = tmp_path / BUFFER_DEFAULT_CACHE_FILE_NAME

        # set max buffer size to 2 events
        charm = make_buffering_charm(cert, buffer_path, buffer_max_events=2)

        # given a charm with a tracing relation and a nonempty buffer
        ctx = Context(charm, meta=charm.META)

        # when we receive many events
        for n in range(n_events):
            ctx.run(ctx.on.start(), State())

            # then the buffer only contains at most 2 spans
            buffer = _Buffer(buffer_path, 100, 100)
            buf = buffer.load()
            assert len(buf) <= 2
