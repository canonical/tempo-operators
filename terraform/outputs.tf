output "app_name" {
  value = juju_application.tempo_coordinator.name
}

output "requires" {
  value = {
    self_tracing      = "self-tracing",
    s3                = "s3",
    logging           = "logging",
    ingress           = "ingress",
    certificates      = "certificates",
    send-remote-write = "send-remote-write",
  }
}

output "provides" {
  value = {
    tempo_cluster     = "tempo-cluster",
    grafana_dashboard = "grafana-dashboard",
    grafana_source    = "grafana-source",
    metrics_endpoint  = "metrics-endpoint",
    tracing           = "tracing",
  }
}