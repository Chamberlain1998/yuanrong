#!/usr/bin/env bash
set -euo pipefail

umask 0027

helper_script="${YR_CONTROLPLANE_HELPER_DIR:-/home/sn/bin/alias}/control_plane_alias.sh"
if [ -f "${helper_script}" ]; then
    bash "${helper_script}" "function_master"
fi

master_ip="${RUNTIME_POD_IP:-$(hostname -i | awk '{print $1}')}"
etcd_addr_list="${YR_ETCD_ADDR_LIST:?Set YR_ETCD_ADDR_LIST for external etcd, e.g. host1:2379,host2:2379}"
services_path="${YR_SERVICES_PATH:-/home/sn/service-config/services.yaml}"
controlplane_cpu_num="${YR_CONTROLPLANE_CPU_NUM:-100}"
meta_service_port="${YR_META_SERVICE_PORT:-31111}"
data_system_deployed="${YR_DATASYSTEM_DEPLOYED:-true}"

ds_worker_args=()
data_system_capability_args=(
  -s "function_master.env.YR_DATASYSTEM_DEPLOYED=\"${data_system_deployed}\""
  -s "function_master.env.YR_BYPASS_DATASYSTEM=\"${YR_BYPASS_DATASYSTEM:-false}\""
  -s "function_proxy.env.YR_DATASYSTEM_DEPLOYED=\"${data_system_deployed}\""
  -s "function_proxy.env.YR_BYPASS_DATASYSTEM=\"${YR_BYPASS_DATASYSTEM:-false}\""
  -s "function_scheduler.env.YR_DATASYSTEM_DEPLOYED=\"${data_system_deployed}\""
  -s "function_scheduler.env.YR_BYPASS_DATASYSTEM=\"${YR_BYPASS_DATASYSTEM:-false}\""
)
case "${data_system_deployed}" in
  false|FALSE|0|no|NO|off|OFF)
    ds_worker_args=(
      -s 'mode.master.ds_worker=false'
    )
    ;;
esac

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

exec /usr/local/bin/yr start \
  --master \
  --block true \
  -s 'mode.master.etcd=false' \
  -s 'mode.master.ds_master=false' \
  -s 'mode.master.function_agent=false' \
  -s 'mode.master.function_scheduler=true' \
  -s 'mode.master.meta_service=true' \
  -s 'mode.master.iam_server=true' \
  "${ds_worker_args[@]}" \
  "${data_system_capability_args[@]}" \
  -s "values.host_ip=\"${master_ip}\"" \
  -s "values.cpu_num=\"${controlplane_cpu_num}\"" \
  -s 'values.etcd.enable_multi_master=true' \
  -s "values.etcd.address=${etcd_addresses}" \
  -s "function_master.args.services_path=\"${services_path}\"" \
  -s "function_proxy.args.services_path=\"${services_path}\"" \
  -s "meta_service.port=${meta_service_port}" \
  -s "function_master.args.enable_traefik_provider=${YR_ENABLE_TRAEFIK_PROVIDER:-false}" \
  -s "function_master.args.traefik_http_entry_point=\"${YR_TRAEFIK_HTTP_ENTRY_POINT:-web}\"" \
  -s "function_master.args.traefik_enable_tls=${YR_TRAEFIK_ENABLE_TLS:-false}" \
  -s "function_master.args.traefik_forward_timeout_ms=${YR_TRAEFIK_FORWARD_TIMEOUT_MS:-3000}" \
  "$@"
