output "app_names" {
  value = merge(
    {
      tempo_s3_integrator     = juju_application.s3_integrator.name,
      tempo_coordinator       = module.tempo_coordinator.app_name,
      tempo_all               = var.monolithic ? module.tempo_all[0].app_name : null,
      tempo_querier           = var.monolithic ? null : module.tempo_querier[0].app_name,
      tempo_query_frontend    = var.monolithic ? null : module.tempo_query_frontend[0].app_name,
      tempo_ingester          = var.monolithic ? null : module.tempo_ingester[0].app_name,
      tempo_distributor       = var.monolithic ? null : module.tempo_distributor[0].app_name,
      tempo_compactor         = var.monolithic ? null : module.tempo_compactor[0].app_name,
      tempo_metrics_generator = var.monolithic ? null : module.tempo_metrics_generator[0].app_name,
    }
  )
  description = "All application names which make up this product module"
}

output "provides" {
  value = {
    tempo_cluster     = "tempo-cluster"
    grafana_dashboard = "grafana-dashboard",
    grafana_source    = "grafana-source",
    metrics_endpoint  = "metrics-endpoint",
    tracing           = "tracing",
  }
  description = "All Juju integration endpoints where the charm is the provider"
}

output "requires" {
  value = {
    logging            = "logging",
    ingress            = "ingress",
    certificates       = "certificates",
    send-remote-write  = "send-remote-write",
    receive_datasource = "receive-datasource"
    catalogue          = "catalogue",
  }
  description = "All Juju integration endpoints where the charm is the requirer"
}
