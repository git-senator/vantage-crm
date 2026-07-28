{{- define "vantage-crm.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "vantage-crm.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "vantage-crm.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "vantage-crm.labels" -}}
app.kubernetes.io/name: {{ include "vantage-crm.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "vantage-crm.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}
{{- end -}}

{{- define "vantage-crm.envFrom" -}}
- configMapRef:
    name: {{ include "vantage-crm.fullname" . }}-config
- secretRef:
    name: {{ .Values.existingSecret }}
{{- end -}}
