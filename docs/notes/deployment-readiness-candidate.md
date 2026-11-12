# Deployment readiness and supervision boundaries

The repository includes reproducible production-container preparation and offline
validation. It does not establish that a cloud host, real worker or Linux local speech
provider is installed. No paid infrastructure is provisioned by these helpers.

The dependency lock targets Python 3.12 on Linux arm64. The backend image runs as a
non-root user, with a read-only application filesystem and private writable state.
Production TLS, provider settings, protected account credentials and model availability
must be configured separately; a healthy process is not sufficient readiness.
Unavailable configured speech must report unavailable rather than silently falling
back to another paid provider. macOS synthesis is unavailable on Linux.

`worker_contract.py` is an off-default presence/fencing protocol, not a general worker
executor. Trusted server-side owner enrollment, authenticated TLS, fresh coordinator
epochs, reconnect generations and expiring heartbeat leases constrain presence.
Workers cannot submit arbitrary owner, command, path or permission grants. Offline
state reports execution unimplemented and termination unproven. Single-host supervisor
locking does not establish cross-host high availability.

Use `check_deployment_readiness.py` for a secret-free prerequisite check. The synthetic
Compose smoke profile disables real providers and uses no production env file, host
credentials or public ports. `test_deployment_container.py` and
`test_deployment_release_offline.py` verify bounded containers, persistence, isolated
restore, native dependency imports, TLS rejection/success, readiness and cooperative
shutdown/recovery. Keep evidence paths private; publish only generic summaries.

Report execution is a separate implemented capability: a locally approved file
allowlist, enum-only plan, fixed read-only report renderer, private artifact verification
and durable task fences. It is not an arbitrary shell or remote file executor. See
[project reports](project-report-vertical-slice.md) and
[Telegram setup](../report-telegram-setup.md).

Remaining physical gates include quiet/spoken recognition and audible response on the
actual device, acoustic AEC, real in-flight native cancellation, and resource/latency
measurement under the chosen operating configuration. Synthetic readiness and mocked
speech cannot close these gates. Keep the installed release and its protected state
intact until a source-reconciled migration is complete and accepted.
