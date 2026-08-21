#!/usr/bin/env bash
set -euo pipefail

umask 0027

node_ip="${HOST_IP:-${RUNTIME_HOST_IP:-$(hostname -i | awk '{print $1}')}}"
master_ip="${YR_MASTER_IP:?Set YR_MASTER_IP to the master service DNS name or IP}"
etcd_addr_list="${YR_ETCD_ADDR_LIST:?Set YR_ETCD_ADDR_LIST for external etcd, e.g. host1:2379,host2:2379}"
services_path="${YR_SERVICES_PATH:-/home/sn/service-config/services.yaml}"
function_proxy_port="${FUNCTION_PROXY_PORT:-22772}"
function_proxy_grpc_port="${FUNCTION_PROXY_GRPC_PORT:-22773}"
ds_worker_port="${DS_WORKER_PORT:-31501}"
runtime_launcher_sock="${RUNTIME_LAUNCHER_SOCK:-/var/run/runtime-launcher.sock}"
data_system_deployed="${YR_DATASYSTEM_DEPLOYED:-true}"

ds_worker_args=(-s "values.ds_worker.port=${ds_worker_port}")
data_system_capability_args=(
  -s "function_proxy.env.YR_DATASYSTEM_DEPLOYED=\"${data_system_deployed}\""
  -s "function_proxy.env.YR_BYPASS_DATASYSTEM=\"${YR_BYPASS_DATASYSTEM:-false}\""
  -s "function_agent.env.YR_DATASYSTEM_DEPLOYED=\"${data_system_deployed}\""
  -s "function_agent.env.YR_BYPASS_DATASYSTEM=\"${YR_BYPASS_DATASYSTEM:-false}\""
)
case "${data_system_deployed}" in
  false|FALSE|0|no|NO|off|OFF)
    ds_worker_args=(
      -s 'mode.agent.ds_worker=false'
    )
    ;;
esac

export RUNTIME_LAUNCHER_SOCK="${runtime_launcher_sock}"
export CONTAINER_EP="${CONTAINER_EP:-unix://${runtime_launcher_sock}}"

function resolve_host() {
  python3 - "$1" <<'PY'
import socket
import sys

print(socket.gethostbyname(sys.argv[1]))
PY
}

function toml_etcd_addresses() {
  local addr_list="$1"
  local result=""
  local sep=""
  local entry host port resolved_host

  IFS=',' read -ra entries <<< "${addr_list}"
  for entry in "${entries[@]}"; do
    host="${entry%:*}"
    port="${entry##*:}"
    resolved_host="$(resolve_host "${host}")"
    result="${result}${sep}{ip=\"${resolved_host}\",peer_port=${port},port=${port}}"
    sep=","
  done

  printf '[%s]' "${result}"
}

etcd_addresses="$(toml_etcd_addresses "${etcd_addr_list}")"
master_scheduler_ip="$(resolve_host "${master_ip}")"

exec /usr/local/bin/yr start \
  --block true \
  --function-proxy-merge-process-enable \
  --data-system-enable "${data_system_deployed}" \
  --enable-runtime-launcher \
  -s "values.host_ip=\"${node_ip}\"" \
  -s "values.function_master.ip=\"${master_scheduler_ip}\"" \
  -s 'values.etcd.enable_multi_master=true' \
  -s "values.etcd.address=${etcd_addresses}" \
  -s "values.function_proxy.port=${function_proxy_port}" \
  -s "values.function_proxy.grpc_listen_port=${function_proxy_grpc_port}" \
  "${ds_worker_args[@]}" \
  "${data_system_capability_args[@]}" \
  -s "function_proxy.args.services_path=\"${services_path}\"" \
  -s 'function_proxy.args.enable_traefik_registry=true' \
  -s 'function_proxy.args.traefik_etcd_prefix="traefik"' \
  -s 'function_proxy.args.traefik_http_entrypoint="web"' \
  -s 'function_proxy.args.traefik_enable_tls=false' \
  "$@"
