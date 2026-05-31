# Incident Runbook: Restore Drill Failure

## Symptoms

`make restore-drill` exits non-zero, the restore-drill CronJob fails, or Prometheus fires `RestoreDrillFailed`.

## Inspect

    kubectl get jobs,pods -n restore-drill
    kubectl logs -n restore-drill job/<job-name>
    kubectl get pods -n restore-drill -l restore-drill/ephemeral=true

For local Docker runtime:

    restore-drill run --config backup/restore-drill/drills/local-redis-aof.yaml --runtime docker --no-cleanup --format json

## Likely Causes

The backup artifact is missing, object-storage credentials are wrong, the restore image lacks Redis/PostgreSQL/MySQL tools, or a validation check no longer matches the restored data.

## Mitigation

Rerun with `--no-cleanup`, inspect the retained target pod or container, fix credentials or backup source paths, and only then update checks if the data contract intentionally changed.

## Evidence

Save the run JSON, compliance report, retained pod name, relevant logs, and backup artifact timestamp.

