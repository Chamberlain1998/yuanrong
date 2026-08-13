# exec

Invoke an agent (function) and stream the response via SSE; enters interactive mode when `--args` is omitted.

## Usage

```shell
adx exec [OPTIONS]
```

## Parameters

* `--agent`: The agent to invoke, in the format `0@namespace@funcname[:version]`; the CLI validates the format and reports an error if invalid.
* `--server`: Frontend address in `host:port` format, e.g. `127.0.0.1:31180` (http is assumed, no scheme needed); you can also explicitly pass `https://host:port` to use HTTPS.
* `--session-ctx`: Agent session context, up to 63 characters; only sent when provided as the `X-Session-Context` header; auto-generated in interactive mode.
* `--session-id`: Instance session ID, up to 63 characters; optional, if not provided the `X-Instance-Session` header is not sent; auto-generated in interactive mode.
* `--session-ttl`: Instance session TTL, default 90; default 600 seconds in interactive mode; must be used with `--session-id` when specified on the command line.
* `--concurrency`: Instance session concurrency, default 1; must be used with `--session-id` when specified on the command line.
* `--args`: Handler arguments as a JSON string; omit to enter interactive mode.

## Notes

* Only `--agent` and `--server` are required; all others are optional.
* `--session-ttl` / `--concurrency` must be used with `--session-id`; if only they are provided without `--session-id`, `adx` will report an error and exit (exit code 2) **without sending any request**.
* When `--args` is provided, a one-shot invocation is performed using the JSON string as the request body.
* When `--args` is omitted, interactive mode is entered; each user input is automatically wrapped as `{"message":"user input"}` via standard JSON serialization and sent as a single invocation. Special characters (quotes, backslashes, newlines, etc.) are properly escaped.
* In interactive mode, if `--session-ctx` is not provided, a session context is auto-generated and the same `X-Session-Context` header is carried in each invocation; if provided, the user's value is used.
* In interactive mode, if `--session-id` is not provided, an InstanceSession ID is auto-generated. Each regular message under the same SessionCtx carries the same `X-Instance-Session`; if `--session-ttl` is not specified, 600 seconds is used.
* After switching SessionCtx via `/sessions`, `/fork`, or `/new`, the CLI sends a release call with `sessionTTL` set to 0 using the original SessionCtx's InstanceSession ID, and generates a new InstanceSession ID for the new SessionCtx.
* After at least one regular message has been sent, `/quit` or input end will additionally send a call with `sessionTTL` set to 0 and body `{}`, causing the InstanceSession to expire immediately. If the release call fails, only a warning is logged and exit is not affected; the server ensures idempotency and eventual cleanup.
* Enter `/quit` to exit interactive mode.
* The response is an SSE stream; `adx` continuously outputs as it receives, until the server sends an end marker.

## Interactive SessionCtx Management

In interactive mode, the following commands manage the current Agent's SessionCtx (Linux):

* `/sessions`: List the current Agent's SessionCtx. On Linux TTY, use up/down arrow keys to select, Enter to switch, Esc or `q` to cancel.
* `/history`: Query the most recent Turn input, output, and status for the current SessionCtx.
* `/fork <turn-id> <new-session-ctx-id>`: Create a new SessionCtx from a completed Turn; auto-switches after success.
* `/delete <session-ctx-id>`: Delete a non-current SessionCtx. To delete the current session, switch first via `/new` or `/sessions`.
* `/new [session-ctx-id]`: Switch the CLI's current SessionCtx only; the server-side session is created on the first regular message.

Linux TTY interactive input supports slash command completion: typing `/` or a command prefix displays candidates; use up/down arrow keys to select, press `Tab` or `Enter` to fill the candidate into the input line; press `Enter` again to execute. Commands with close spelling but no prefix match will display up to three similar candidates.

`SessionCtx ID` and `Turn ID` have a maximum length of 63 characters. `/sessions` displays the 50 most recently updated sessions for the current Agent by default; non-TTY environments only print a list, use `/new <session-ctx-id>` to switch.

The target SessionCtx ID for `/fork` must be explicitly provided and cannot be the same as the current SessionCtx ID. When retrying after a timeout, the same source SessionCtx, Turn ID, and target SessionCtx ID should be used.

## Examples

```shell
adx exec --agent <AGENT> --server 127.0.0.1:31180
```

```shell
adx exec --agent <AGENT> --server 127.0.0.1:31180 --args '{"message":"hello"}'
```

```shell
adx exec --agent <AGENT> --server 127.0.0.1:31180 \
        --session-ctx ctx1 --session-id id1 --session-ttl 90 --concurrency 1 \
        --args '{"param1":"hello"}'
```

```console
$ adx exec --agent 0@default@demo --server 127.0.0.1:31180 --session-ctx research-main
[research-main] > /history
[research-main] > /fork turn-0001 research-alt
[research-alt] > Ignore previous conclusions, check dependency security issues instead
[research-alt] > /delete research-main
```
