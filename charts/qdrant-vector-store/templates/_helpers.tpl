{{- define "qdrant-vector-store.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "qdrant-vector-store.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- include "qdrant-vector-store.name" . -}}
{{- end -}}
{{- end -}}

{{- define "qdrant-vector-store.labels" -}}
app.kubernetes.io/name: {{ include "qdrant-vector-store.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/part-of: private-ai-platform-kit
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.podLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{- define "qdrant-vector-store.selectorLabels" -}}
app.kubernetes.io/name: {{ include "qdrant-vector-store.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "qdrant-vector-store.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "qdrant-vector-store.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}
