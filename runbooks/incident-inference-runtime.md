# Incident Runbook: Inference Runtime Unavailable

## Symptoms

Gateway smoke tests fail, `/healthz` succeeds but `POST /v1/chat/completions` returns 502, or Grafana shows high gateway error rate.

## Inspect

    kubectl get pods -n inference
    kubectl logs -n inference deploy/inference-gateway-inference-gateway
    kubectl logs -n inference deploy/inference-gateway-inference-gateway --tail=200 | grep inference_request
    kubectl get pods -n ollama
    kubectl get pods -n vllm
    kubectl describe pod -n vllm -l app.kubernetes.io/name=vllm

## Likely Causes

The selected runtime service is unavailable, a model is still loading, the vLLM pod is waiting for GPU capacity, or the gateway points at the wrong runtime URL.

## Mitigation

Switch the gateway to the healthy backend by updating `RUNTIME_BACKEND` values in the environment overlay, then sync Argo CD. For vLLM GPU pending issues, follow `runbooks/gpu-capacity.md`.

## Evidence

Capture gateway logs, the `inference_request` audit event for the failed `request_id`, runtime pod events, Argo CD application health, and the failed smoke-test response.
