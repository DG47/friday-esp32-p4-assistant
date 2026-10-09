# Future project-runner containment design (unbuilt)

The existing runner accepts only original registered fixture bytes. It does not safely run selected-project or generated code. No container or new runtime was installed.

A future runner must start denied by default in a separate OS isolation boundary with an unprivileged identity: no host home, clipboard, credentials, sockets, devices, network, process namespace or writable host mounts. Mount an explicitly selected immutable source snapshot read-only and a disposable output volume; export only reviewed patch paths and bounded structured summaries. Dependency/runtime images require provenance and fixed digests. No package installation or network access is implied by project selection.

Before execution, display and approve the concrete repository revision, allowed read/write paths, exact commands, runtime/dependency digests, CPU/memory/process/output/time budgets and any separately requested egress endpoint. Generated patches require validation and a fresh approval if scope changes. Bind permission to task/revision/digest/nonce/expiry; Stop must revoke admission and terminate the isolated process group before reporting stopped.

Persist launch intent and supervisor identity outside the child before launch; use durable kernel-supported process/container identity rather than a bare reused PID. Reconcile crashes conservatively as uncertain until actual termination evidence exists. Do not prune ownership or uncertain side-effect evidence. A supervisor must recover and drain outstanding workers before admitting replacements.

Egress must be deny-by-default: structured allowlisted status, secret filtering, size bounds, approved recipients and explicit review of exported patches. Raw output is never a transport default. Validate isolation using independent escape and denial tests with synthetic data, including exec replacement, mount traversal, runtime imports, process spawning and network. Passing current fixture probes supplies no clearance for this design. Integration, authentication, permission enrollment and these containment tests remain unbuilt.

Separate permissions needed before implementation:

- Read/snapshot access to one explicitly selected repository and disposable output/evidence roots.
- Runtime installation or system privilege only if a chosen isolation boundary requires it.
- Dependency downloads and network access separately; default none.
- Provider transmission, inference and cost budget separately.
- Transport recipient enrollment and protected credential use separately.

No full-disk, clipboard, Automation or administrator permission is implied.
