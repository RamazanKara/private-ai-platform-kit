{{- define "inference-gateway.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "inference-gateway.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "inference-gateway.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "inference-gateway.labels" -}}
app.kubernetes.io/name: {{ include "inference-gateway.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/component: gateway
app.kubernetes.io/part-of: ai-platform-ops-lab
app.kubernetes.io/managed-by: {{ .Release.Service }}
platform.ai/cost-center: {{ index .Values.podLabels "platform.ai/cost-center" | quote }}
platform.ai/environment: {{ index .Values.podLabels "platform.ai/environment" | quote }}
platform.ai/owner: {{ index .Values.podLabels "platform.ai/owner" | quote }}
platform.ai/sandbox-id: {{ default .Values.traceability.defaultSandboxId (index .Values.podLabels "platform.ai/sandbox-id") | quote }}
{{- end -}}

{{- define "inference-gateway.selectorLabels" -}}
app.kubernetes.io/name: {{ include "inference-gateway.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "inference-gateway.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "inference-gateway.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}
