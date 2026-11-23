# Native tenant runtime

The trusted host gateway owns one `TenantRuntime` instance. Enable ordinary tenants with
`SEED_NATIVE_LOCAL_TENANTS=1` and set `TENANT_RUNTIME_SECRET` to a stable random secret of
at least 32 bytes. Keep the secret in the gateway environment and backed up with its
deployment configuration. Set `TENANT_RUNTIME_STATE_DIR` to the gateway's absolute
state directory to keep provisioning locks stable across launches. Without it,
the runtime uses the OS temporary directory. The secret derives distinct API,
PostgreSQL, repository relay, and model relay credentials per account. Changing it
requires migrating the existing database credentials.

Build the native image from the app root on the local Docker engine.

```powershell
docker build --tag careerpilot-tenant-native:parity-20261124 -f deploy/tenant/Dockerfile .
```

Use the immutable workspace slug from the verified account. The optional display role
initializes an empty workspace. Changes to the native profile are preserved.

| Host call | Result |
| --- | --- |
| `runtime.ensure(workspace_slug, display_role)` | Tenant with a loopback `base_url`, `api_token`, `workspace_slug` and native `headers`. |
| `with runtime.database(workspace_slug, display_role) as url` | Temporary PostgreSQL connection URL for a trusted transfer. Dispose the client and exit the context when done. |
| `runtime.remove(workspace_slug)` | Deletes only the selected tenant's Docker project resources and host listeners. Safe to retry or call for an unused account. |
| `runtime.close()` | Closes host listeners while retaining tenant data. Call after gateway requests have drained. |

Call blocking methods in a thread when the gateway is async. Send upstream requests to
`base_url + "/api/v1/" + path` using the returned native headers. Resolve the account in the
gateway before provisioning or proxying. Serialize verified account deletion with request
admission for that account, then call `remove`. Run the gateway as the trusted local service
account with Docker access. The public web server does not need Docker access.

Each account has native API and worker containers, a PostgreSQL instance, and its own
internal Docker bridge and volumes. The API and PostgreSQL are reached through
host-only loopback relays. The native question bank starts empty, and the gateway's
transfer code supplies the account's existing private questions. Native settings,
projects, upload/import records, and worker grading files persist in the tenant volumes.

For model access, supply `SEED_MODEL_BASE_URL`, `SEED_MODEL_KEY`, and `SEED_MODEL_NAME`
together in the trusted gateway environment. The base URL must be the approved external
HTTPS API base. The per-account model relay alone receives the upstream key and joins
a dedicated egress network. The native API and worker use a distinct relay token and
stay on the internal bridge. With all three settings absent, native rule mode is used.

Repository reads for native GitHub and GitCode project scans go through the repository
sidecar, which starts even in rule mode. The sidecar has a separate egress bridge
and its own per-account relay token. It accepts supported read operations and can
forward the member's own repository credentials. It receives no owner credentials or
model key. The native API and worker remain on the private bridge.
