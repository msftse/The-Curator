{{/*
Common template helpers for the agentic-skill-hub umbrella chart.

Naming convention:
  - Chart-level fullname  = <release>-skillhub  (trimmed to 63 chars)
  - Per-component name    = <release>-skillhub-<component>
  - Component label       = app.kubernetes.io/component=<component>

All workloads share `app.kubernetes.io/part-of=skillhub` for cross-cutting
selection (e.g. `kubectl get pod -l app.kubernetes.io/part-of=skillhub`).
*/}}

{{- define "skillhub.fullname" -}}
{{- $name := default "skillhub" .Chart.Name | trunc 50 | trimSuffix "-" -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "skillhub.componentName" -}}
{{- $component := .component | required "componentName: .component is required" -}}
{{- printf "%s-%s" (include "skillhub.fullname" .) $component | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "skillhub.commonLabels" -}}
app.kubernetes.io/name: skillhub
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: skillhub
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "skillhub.componentLabels" -}}
{{ include "skillhub.commonLabels" . }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "skillhub.selectorLabels" -}}
app.kubernetes.io/name: skillhub
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
Workload-identity pod label. AKS's workload-identity webhook only injects
the federated-token volume + projected SA token into pods carrying
`azure.workload.identity/use=true`. Without this label, MSAL-on-pod gets
no token. Render this on every pod.
*/}}
{{- define "skillhub.wiPodLabels" -}}
azure.workload.identity/use: "true"
{{- end -}}

{{/*
Image reference helper — composes `{registry}/{repo}:{tag}`.
Call as: `{{ include "skillhub.image" (dict "Values" .Values "component" "backend") }}`
*/}}
{{- define "skillhub.image" -}}
{{- $repo := index .Values.image.repositories .component | required (printf "image.repositories.%s is required" .component) -}}
{{- $registry := .Values.global.imageRegistry | required "global.imageRegistry is required" -}}
{{- printf "%s/%s:%s" $registry $repo .Values.image.tag -}}
{{- end -}}

{{/*
Workload identity client id for a component. Errors loudly if missing.
*/}}
{{- define "skillhub.wiClientId" -}}
{{- $key := printf "%sClientId" .component -}}
{{- $id := index .Values.global.workloadIdentity $key -}}
{{- if not $id -}}
  {{- fail (printf "global.workloadIdentity.%s is required (set from infra/main.bicep output)" $key) -}}
{{- end -}}
{{- $id -}}
{{- end -}}

{{/*
Workload identity principal (object) id for a component. Used as the
Redis Entra username (the principal OID is the Entra ACL identity), as
the Cosmos data-plane RBAC subject, and as the Blob delegator. Errors
loudly if missing for non-frontend components — the frontend never
authenticates to Azure data planes.

Call as: `{{ include "skillhub.wiPrincipalId" (dict "Values" .Values "component" "backend") }}`
*/}}
{{- define "skillhub.wiPrincipalId" -}}
{{- $oid := index .Values.global.workloadIdentityObjectIds .component -}}
{{- if not $oid -}}
  {{- fail (printf "global.workloadIdentityObjectIds.%s is required (set from infra/main.bicep output)" .component) -}}
{{- end -}}
{{- $oid -}}
{{- end -}}

{{/*
Standard pod securityContext block. Applied to every workload.
*/}}
{{- define "skillhub.podSecurityContext" -}}
runAsNonRoot: {{ .Values.podSecurity.runAsNonRoot }}
runAsUser: {{ .Values.podSecurity.runAsUser }}
fsGroup: {{ .Values.podSecurity.fsGroup }}
seccompProfile:
  type: {{ .Values.podSecurity.seccompProfile }}
{{- end -}}

{{- define "skillhub.containerSecurityContext" -}}
allowPrivilegeEscalation: {{ .Values.podSecurity.allowPrivilegeEscalation }}
readOnlyRootFilesystem: {{ .Values.podSecurity.readOnlyRootFilesystem }}
capabilities:
  drop:
    - ALL
{{- end -}}

{{/*
Render a flat `env:` list from a `map[string]string`. Empty-string values
are skipped so optional config doesn't poison the pod spec. Callers may
need to append additional env entries (e.g. valueFrom: secretKeyRef) after.

Call as: `{{ include "skillhub.envFromMap" .Values.backend.env | nindent 12 }}`
*/}}
{{- define "skillhub.envFromMap" -}}
{{- range $k, $v := . -}}
{{- if $v }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
SecretProviderClass mount volume. Renders the `volumes:` entry that mounts
the SPC at /mnt/secrets-store/ in read-only mode. The SPC must exist in
the same namespace (rendered by templates/<component>/secretproviderclass.yaml).
*/}}
{{- define "skillhub.spcVolume" -}}
- name: secrets-store
  csi:
    driver: secrets-store.csi.k8s.io
    readOnly: true
    volumeAttributes:
      secretProviderClass: {{ printf "%s-secrets" (include "skillhub.componentName" .) }}
{{- end -}}

{{- define "skillhub.spcVolumeMount" -}}
- name: secrets-store
  mountPath: /mnt/secrets-store
  readOnly: true
{{- end -}}
