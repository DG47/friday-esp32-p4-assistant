# Preserve installed state when publishing or migrating source

A code snapshot is not an installed-release backup. Keep the currently running
checkout, supervisor, configuration, credentials, device identity and databases
unchanged while preparing an independent candidate. Publish source/tests and generic
runbooks only; do not import local Git history, account configuration or state.

Before a future migration, record source and artifact identities locally, preserve the
current service, take a protected online SQLite backup and verify restoration into a
separate private path. Never select a stale database merely because it is near a source
checkout. Keep one supervisor for the selected state database, and stop its predecessor
before transferring ownership. Fresh coordinator epochs and worker/task fences must
reject pre-restart sessions and expired leases. Cancellation must drain admitted native
or storage work before ownership is released; request cancellation alone does not prove
that physical work or external effects stopped.

Reconcile source by path and content against the installed release and upstream main.
Preserve paired-device authentication, mutation tap ownership, capture diagnostics,
media_updates_v1, glyph sanitization and sequential full-fact memory review. Link the
native experience guard in the firmware build. Source parity does not establish
production configuration, TLS trust, native pixel parity or audible voice acceptance.

Container validation should use original synthetic inputs, disabled/mocked account
providers, no external network or host credential mounts, a non-root user, read-only
root and bounded writable state. Verify resource limits, backup/restore integrity,
trusted TLS success, wrong-host/untrusted-TLS rejection, shutdown drain and recovery.
`test_deployment_release_offline.py` exercises these boundaries without starting the
production Compose profile. Its model/worker handlers are synthetic; it does not prove
real inference, acoustic AEC, audible response or exactly-once external effects.

Ordinary authorized code publication/deployment is distinct from user-entered token
configuration, real-folder enrollment, persistent account access and paid procurement.
Those integrations require the owner's specific handoff. Preserve rollback until real
physical voice/device and account acceptance are complete.
