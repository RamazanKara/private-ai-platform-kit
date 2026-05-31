{{- define "rag-service.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "rag-service.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "rag-service.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "rag-service.labels" -}}
app.kubernetes.io/name: {{ include "rag-service.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/component: rag
app.kubernetes.io/part-of: private-ai-platform-kit
app.kubernetes.io/managed-by: {{ .Release.Service }}
platform.ai/cost-center: {{ index .Values.podLabels "platform.ai/cost-center" | quote }}
platform.ai/environment: {{ index .Values.podLabels "platform.ai/environment" | quote }}
platform.ai/owner: {{ index .Values.podLabels "platform.ai/owner" | quote }}
platform.ai/sandbox-id: {{ default .Values.traceability.defaultSandboxId (index .Values.podLabels "platform.ai/sandbox-id") | quote }}
{{- end -}}

{{- define "rag-service.selectorLabels" -}}
app.kubernetes.io/name: {{ include "rag-service.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "rag-service.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "rag-service.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}
