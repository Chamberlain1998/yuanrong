# Delete Agent Instance

## Description

This API is used to delete an agent instance in the openYuanrong cluster. It reaches the instance directly by `instance_id`. Both creation modes (inline / registered) delete the same way.

## Constraints

- `instanceId` must be the `instance_id` (UUID) returned when the agent instance was created. A request to `DELETE /api/agent/` (empty trailing segment) is handled by the routing layer as 404; the handler's 400 branch for an empty `instanceId` is defensive logic.
- A non-existent or already-deleted `instanceId` returns 404 rather than 200/204 — this is intentional, to prevent retries from mistaking "already deleted" for success. Clients sensitive to idempotency should treat 404 as the terminal state (resource absent), not a failure.
- A malformed `instanceId` (non-UUID) is determined by route matching and generally returns 404.
- Authentication goes through frontend's global `GlobalJWTAuthMiddleware`, consistent with other function service REST APIs. When `enable_func_token_auth` is on, a valid JWT must be carried (see the auth section of [Agent Instance Protocol Invocation Channels](./agent_invoke_channels.md)).

## URI

`DELETE /api/agent/:instanceId`

## Request Parameters

### Request Path Parameters

| **Parameter** | **Required** | **Type** | **Description** |
| -------- | ---------- | ---------- | ----------- |
| instanceId | Yes | string | Instance ID (the UUID returned at creation). |

## Response Parameters

| **Name** | **Type** | **Description** |
| -------- | -------- | -------- |
| code | int | Status code; `200` means success. The HTTP status code on success is `200`. |
| status | String | Status. `deleted` means deleted. |
| message | String | Error message on failure. |

## Examples

```bash
curl -X DELETE http://{frontend}:8888/api/agent/0b6c6322-6533-4901-8000-00000000bb0b
# When auth is on, carry: -H "X-Auth: <jwt>"
```

> The example shows an in-cluster plaintext call with auth off; for production use `https://` and enable `enable_func_token_auth`, carrying a valid JWT.

Response:

```json
{"code":200,"status":"deleted"}
```

## Error Codes

| **HTTP status** | **Description** |
| -------- | -------- |
| 200 | OK. The instance has been deleted. |
| 401 | Unauthorized. When `enable_func_token_auth` is on, no JWT or an invalid JWT — returned by the global `GlobalJWTAuthMiddleware` before reaching the handler. |
| 403 | Forbidden. The JWT is valid but the caller lacks permission. |
| 404 | Not Found. `instanceId` does not exist or has already been deleted; a `DELETE /api/agent/` (empty trailing segment) is also returned as 404 by the routing layer. |
| 500 | Internal Server Error. `message` is of the form `failed to delete agent: <cause>`: container stop/remove error or other deletion failure. |
