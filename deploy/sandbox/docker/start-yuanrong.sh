#!/usr/bin/env bash

set -euo pipefail

AIO_NODE_IP="$(hostname -i | awk '{print $1}')"
ENABLE_TOKEN="${ENABLE_TOKEN:-false}"

exec /usr/local/bin/yr start \
  --master \
  --block true \
  -s "values.host_ip=\"${AIO_NODE_IP}\"" \
  -s 'function_master.args.services_path="/openyuanrong/services.yaml"' \
  -s 'function_proxy.args.services_path="/openyuanrong/services.yaml"' \
  -s 'function_master.args.enable_traefik_provider=true' \
  -s 'function_master.args.traefik_http_entry_point="web"' \
  -s 'function_master.args.traefik_enable_tls=false' \
  -s 'function_master.args.traefik_forward_timeout_ms=3000' \
  -s 'mode.master.frontend=true' \
  -s 'values.frontend.port=8889' \
  -s 'frontend.ssl_enable=false' \
  -s 'frontend.client_auth_type="NoClientCert"' \
  -s "values.frontend.enable_function_token_auth=${ENABLE_TOKEN}" \
  -s "values.frontend.sandbox_router_enable_jwt=${ENABLE_TOKEN}" \
  -s "values.frontend.sandbox_router_validate_iam=${ENABLE_TOKEN}" \
  -s 'mode.master.function_scheduler=true' \
  -s 'values.function_scheduler.lease_port=8890' \
  -s 'mode.master.meta_service=true' \
  -s "mode.master.iam_server=${ENABLE_TOKEN}"
