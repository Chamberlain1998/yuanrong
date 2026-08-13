# deploy

Register an agent (function) to the openYuanrong cluster via meta_service.

## Usage

```shell
adx deploy [OPTIONS]
```

## Parameters

* `-s, --spec`: Function definition, either an inline JSON string or a JSON file path (auto-detected); invalid JSON or non-existent file will cause an error.
* `--server`: meta_service address in `host:port` format, e.g. `127.0.0.1:31182` (http is assumed, no scheme needed); you can also explicitly pass `https://host:port` to use HTTPS; invalid format will cause an error.

## Notes

* JWT authentication is supported via the global `--jwt-token` option or the `YR_JWT_TOKEN` environment variable, sent in the `X-Auth` request header. Using the environment variable is recommended to avoid token leakage in process lists or shell history.
* `-s/--spec` performs format validation: if the value is an existing file, it reads and parses the JSON from the file; otherwise, it parses as inline JSON (i.e. file path takes priority). If neither condition is met (neither valid JSON nor an existing file path), it exits with an error (exit code 2).
* `--server` must be in `host:port` format (missing port or invalid port will cause an error). HTTP is used by default; to use HTTPS, explicitly pass `https://host:port`.
* If the `enableSessionCtx` field is not set in the function definition, it defaults to `true`; if explicitly set (to `true` or `false`), the user's value is preserved.
* Upon successful registration, the public agent name is printed in the format `0@namespace@funcname`, which can be used directly with `adx exec --agent`.

## Exit Codes

| Exit Code | Meaning |
| --------- | ------- |
| `0` | Success |
| `1` | Server failure (HTTP non-2xx, or response `code != 0`) |
| `2` | Parameter error (invalid JSON, file not found, invalid `--server` format, missing required parameter) |
| `3` | Network error (connection failure, timeout) |

## Examples

```shell
adx deploy -s ./agent.json --server 127.0.0.1:31182
```

```shell
adx deploy -s '{"name":"0@faaspy@demo","runtime":"python3.11","handler":"demo.handler"}' \
      --server 127.0.0.1:31182
```
