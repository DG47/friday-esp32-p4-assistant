# Hosting capacity and cost envelope

No cloud account inventory, credentials, funded capacity or spending authorization is
included in this source publication. No cloud resources are created by this runbook.
Choose infrastructure only after separately approving its costs and persistent access.

Measure one-device steady load, cold/warm transcription and model latency, queue growth,
RAM, CPU, disk and shutdown drain. A coordinator with speech on a local Mac has different
capacity needs from a host performing continuous transcription. CPU/RAM hypotheses are
not benchmarks. Include retained backups, restore capacity, transfer, logs, public IPs,
model/speech charges, taxes and simultaneous rollback hosts in any later proposal.
Recheck official provider pricing at selection; budgets and alerts are not hard spend caps.

Uninterrupted 16 kHz mono 16-bit PCM is 32,000 bytes/second; 24 kHz is 48,000 bytes/second,
before framing and retries. These upper-duration assumptions are not observed traffic
or an authorization for always-on transmission. Persistent SQLite needs local durable
disk, one owner and tested private backup/restore. Keep encryption keys separate from
backups. Benchmark capacity before choosing a paid host; no paid fallback belongs to
local-only reasoning policy.
