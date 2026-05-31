# restore-drill Integration

Private AI Platform Kit uses `RamazanKara/restore-drill` for application-data restore verification. Velero covers Kubernetes resource and persistent-volume recovery; restore-drill proves that backup artifacts can be restored into disposable targets and validated with data checks.

Local run:

    make restore-drill RUNTIME=local

Kubernetes run:

    kubectl apply -f backup/restore-drill/k8s/

Reports are written under `results/restore-drill/` for local runs and `/reports` inside the Kubernetes CronJob pod for scheduled runs. Prometheus metrics are pushed to `http://pushgateway.monitoring:9091`.

