output "app_name" {
  value = juju_application.tempo_coordinator.name
}

output "provides" {
  value = {
    grafana_dashboard = "grafana-dashboard",
    grafana_source    = "grafana-source",
    metrics_endpoint  = "metrics-endpoint",
    tempo_cluster     = "tempo-cluster",
    tracing           = "tracing",
  }
}

output "requires" {
  value = {
    certificates          = "certificates",
    ingress               = "ingress",
    logging               = "logging",
    s3                    = "s3",
    self_charm_tracing    = "self-charm-tracing",
    self_workload_tracing = "self-workload-tracing",
    send_remote_write     = "send-remote-write",
    receive_datasource    = "receive-datasource"
    catalogue             = "catalogue",
  }
}