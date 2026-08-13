# CLI - the adx command

`adx` is the command-line tool for Agent Distributed Executor (agent-dx). Agents are essentially functions, and `adx` wraps function registration and invocation as calls to the underlying openYuanrong function service interface.

Install `adx`:

```bash
pip install agent_dx_cli
```

## Commands

| Command | Description |
| ------- | ----------- |
| [`deploy`](./adx_deploy.md) | Register an agent (function) via the meta_service component. |
| [`exec`](./adx_exec.md) | Invoke an agent (function) and stream the response via SSE; enters interactive mode when `--args` is omitted. |

## Global Options

| Option | Description |
| ------ | ----------- |
| `--jwt-token` | JWT authentication token; can also be provided via the `YR_JWT_TOKEN` environment variable, sent in the `X-Auth` request header. Using the environment variable is recommended to avoid token leakage in process lists or shell history. |
| `-v, --verbose` | Enable DEBUG logging; prints request details (method, url, headers, body) before sending. Sensitive headers such as `X-Auth` are replaced with `<redacted>` to prevent credential leakage. |
| `--version` | Print the `adx` version and exit. |
| `-h, --help` | Show help information. |

```{eval-rst}
.. toctree::
  :hidden:

  adx_deploy
  adx_exec
```

## Exit Codes

| Exit Code | Meaning |
| --------- | ------- |
| `0` | Success |
| `1` | Server failure (HTTP non-2xx, or response `code != 0`) |
| `2` | Parameter error (invalid JSON, file not found, missing required parameter) |
| `3` | Network error (connection failure, timeout) |
