output "app_names" {
  value = merge(
    {
      tempo_s3_integrator     = juju_application.s3_integrator.name,
      tempo_coordinator       = module.tempo_coordinator.app_name,
      tempo_querier           = length(module.tempo_querier) == 0 ? null : module.tempo_querier[0].app_name
      tempo_query_frontend    = length(module.tempo_query_frontend) == 0 ? null : module.tempo_query_frontend[0].app_name
      tempo_ingester          = length(module.tempo_ingester) == 0 ? null : module.tempo_ingester[0].app_name
      tempo_distributor       = length(module.tempo_distributor) == 0 ? null : module.tempo_distributor[0].app_name
      tempo_compactor         = length(module.tempo_compactor) == 0 ? null : module.tempo_compactor[0].app_name
      tempo_metrics_generator = length(module.tempo_metrics_generator) == 0 ? null : module.tempo_metrics_generator.app_name
      tempo_all               = length(module.tempo_all) == 0 ? null : module.tempo_all[0].app_name
    }
  )
  description = "All application names which make up this product module"
}

output "endpoints" {
  value = {
    # Requires
    logging            = "logging",
    ingress            = "ingress",
    certificates       = "certificates",
    send-remote-write  = "send-remote-write",
    receive_datasource = "receive-datasource"
    catalogue          = "catalogue",

    # Provides
    tempo_cluster     = "tempo-cluster"
    grafana_dashboard = "grafana-dashboard",
    grafana_source    = "grafana-source",
    metrics_endpoint  = "metrics-endpoint",
    tracing           = "tracing",
  }
  description = "All Juju integration endpoints which make up this product module"
}
