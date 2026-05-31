{{- define "ollama.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "ollama.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "ollama.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "ollama.labels" -}}
app.kubernetes.io/name: {{ include "ollama.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/component: runtime
app.kubernetes.io/part-of: private-ai-platform-kit
app.kubernetes.io/managed-by: {{ .Release.Service }}
platform.ai/cost-center: {{ index .Values.podLabels "platform.ai/cost-center" | quote }}
platform.ai/environment: {{ index .Values.podLabels "platform.ai/environment" | quote }}
platform.ai/owner: {{ index .Values.podLabels "platform.ai/owner" | quote }}
platform.ai/sandbox-id: {{ default "local-lab" (index .Values.podLabels "platform.ai/sandbox-id") | quote }}
{{- end -}}

{{- define "ollama.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ollama.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "ollama.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "ollama.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}
