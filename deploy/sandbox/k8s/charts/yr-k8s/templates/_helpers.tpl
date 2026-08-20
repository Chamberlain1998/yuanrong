{{- define "yr-k8s.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "yr-k8s.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "yr-k8s.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "yr-k8s.namespace" -}}
{{- default .Release.Namespace .Values.global.namespace.name -}}
{{- end -}}

{{- define "yr-k8s.labels" -}}
app.kubernetes.io/name: {{ include "yr-k8s.name" . }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name (.Chart.Version | replace "+" "_") }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.global.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{- define "yr-k8s.componentName" -}}
{{- printf "%s-%s" (include "yr-k8s.fullname" .root) .component | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "yr-k8s.serviceAccountName" -}}
{{- $values := .values -}}
{{- if $values.serviceAccount.create -}}
{{- default (include "yr-k8s.componentName" (dict "root" .root "component" .component)) $values.serviceAccount.name -}}
{{- else if $values.serviceAccount.name -}}
{{- $values.serviceAccount.name -}}
{{- else -}}
{{- fail (printf "%s.serviceAccount.name must be set when %s.serviceAccount.create=false" .component .component) -}}
{{- end -}}
{{- end -}}

{{- define "yr-k8s.image" -}}
{{- $registry := trimSuffix "/" (default .root.Values.global.imageRegistry .image.registry) -}}
{{- $repository := .image.repository -}}
{{- $tag := .image.tag | default "latest" -}}
{{- if $registry -}}
{{- printf "%s/%s:%s" $registry $repository $tag -}}
{{- else -}}
{{- printf "%s:%s" $repository $tag -}}
{{- end -}}
{{- end -}}

{{- define "yr-k8s.runtimeImage" -}}
{{- $override := get .root.Values.global.runtimeImages .suffix | default dict -}}
{{- $image := mergeOverwrite (deepCopy .root.Values.global.images.runtime) $override -}}
{{- include "yr-k8s.image" (dict "root" .root "image" $image) -}}
{{- end -}}

{{- define "yr-k8s.imagePullSecrets" -}}
{{- with .Values.global.imagePullSecrets }}
imagePullSecrets:
{{ toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{- define "yr-k8s.anyCoreComponentEnabled" -}}
{{- if or .Values.etcd.enabled .Values.master.enabled .Values.frontend.enabled .Values.node.enabled .Values.traefik.enabled -}}true{{- else -}}false{{- end -}}
{{- end -}}

{{- define "yr-k8s.etcdServiceName" -}}
{{- include "yr-k8s.componentName" (dict "root" . "component" "etcd") -}}
{{- end -}}

{{- define "yr-k8s.etcdAddrList" -}}
{{- if .Values.global.externalEtcd.addrList -}}
{{- .Values.global.externalEtcd.addrList -}}
{{- else -}}
{{- printf "%s.%s.svc.cluster.local:%v" (include "yr-k8s.etcdServiceName" .) (include "yr-k8s.namespace" .) .Values.etcd.service.port -}}
{{- end -}}
{{- end -}}

{{- define "yr-k8s.etcdMetaStoreAddress" -}}
{{- if .Values.global.externalEtcd.metaStoreAddress -}}
{{- .Values.global.externalEtcd.metaStoreAddress -}}
{{- else -}}
{{- printf "http://%s" (include "yr-k8s.etcdAddrList" .) -}}
{{- end -}}
{{- end -}}
{{- define "yr-k8s.validateDataSystem" -}}
{{- if and (not .Values.global.dataSystem.enabled) (not .Values.global.dataSystem.bypass) -}}
{{- fail "YR_BYPASS_DATASYSTEM must be true when YR_DATASYSTEM_DEPLOYED is false" -}}
{{- end -}}
{{- end -}}
