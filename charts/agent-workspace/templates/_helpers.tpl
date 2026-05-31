{{- define "agent-workspace.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "agent-workspace.labels" -}}
app.kubernetes.io/name: {{ include "agent-workspace.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/component: agent-workspace
app.kubernetes.io/part-of: ai-platform-ops-lab
app.kubernetes.io/managed-by: {{ .Release.Service }}
platform.ai/cost-center: {{ .Values.sandbox.costCenter | quote }}
platform.ai/environment: {{ .Values.sandbox.environment | quote }}
platform.ai/owner: {{ .Values.sandbox.owner | quote }}
platform.ai/sandbox-id: {{ .Values.sandbox.id | quote }}
platform.ai/tenant: {{ .Values.sandbox.tenant | quote }}
platform.ai/compliance-profile: {{ .Values.sandbox.complianceProfile | quote }}
platform.ai/data-classification: {{ .Values.sandbox.dataClassification | quote }}
{{- end -}}

{{- define "agent-workspace.serviceAccountName" -}}
{{- default "agent-runner" .Values.serviceAccount.name -}}
{{- end -}}
