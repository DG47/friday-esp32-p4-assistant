# Spotify authentication: offline-tested operator handoff

The setup helper and token lifecycle support the opt-in runtime documented in
[spotify-runtime.md](spotify-runtime.md). Default access is disabled. Selecting
Spotify requires explicit account-access approval; creating a grant alone does
not enable reads or controls. Tests use synthetic grants and provider responses.
Policy and lyrics rights remain separate from account consent.

## Before any real connection

Confirm the personal app and selected account, developer owner Premium status,
playback eligibility, allowlist entry, the two scopes below, callback address,
and private storage path. The helper cannot independently identify the selected
account without additional identity permissions; verify the account in Spotify's
consent UI. Use a user-owned app, not ChatGPT credentials. Do not supply a client
secret or paste tokens/codes into chat.

Register the explicit redirect `http://127.0.0.1:8765/oauth/spotify/callback` for
that app. The helper binds only that IPv4 loopback address and port. Choose a
separately registered port with `--port` if 8765 is occupied; it does not fall back
silently or bind to the LAN. `localhost` is rejected. The callback window lasts
180 seconds, with bounded socket reads. PKCE S256, a fresh random state, exact
callback path/Host and duplicate-parameter checks protect code handling. Callback
URLs/codes are never logged. Invalid callbacks do not consume the valid attempt;
a state-matched denial stops setup. The temporary listener always closes.

The only requested scopes are:

```
user-read-playback-state user-modify-playback-state
```

Initial exchange must return exactly that set and an offline refresh grant. Extra,
missing or duplicated scopes are refused. Refresh responses may omit scope only
when an already validated exact grant is present; any returned scope must match
exactly. All token requests use the fixed official HTTPS token endpoint, refuse
redirects and limit response size and total/read timeouts.

## User-run connection, after explicit approval

From this candidate, using its compatible Python environment and existing
httpx dependency, replace the public app ID placeholder:

```sh
python scripts/connect_spotify.py connect \
  --client-id YOUR_PUBLIC_SPOTIFY_APP_ID \
  --port 8765 \
  --destination "$HOME/.local/share/friday/spotify/credentials.json" \
  --approve-account-access
```

The approval flag is a literal approval gate; omit it for an intentional refusal with zero storage or
consent I/O. The helper validates storage before opening the browser. An existing
file or symlink is refused, preserving prior credentials. The helper opens the
system browser for consent, exchanges the code and stores only the public client
ID, validated scopes, refresh token and local enabled flag. Access tokens, codes
and PKCE verifier are not persisted. No runtime config/service change follows.

Storage is an owner-only mode 0700 directory and regular mode 0600 file, checked
with directory/file descriptors and no-follow opens. It is **not encrypted at
rest**; owner access, disk backups and compromise of the Mac remain exposure
paths. No Keychain installation, trust change or new OS permission was attempted.
The example uses a location outside this repository; data/spotify is also ignored
as a defensive fallback. Do not copy this file into firmware or commit it.

## Refresh and rotation

`TokenManager(path, injected_client)` supplies short-lived in-memory access tokens.
It checks the saved enabled flag before every return, serializes callers with an
async lock, and holds an owner-only advisory file lock across refresh/rotation.
Another manager/process contending for the grant fails as `busy` rather than
rotating simultaneously. It rereads the latest refresh token under that lock.
Rotated grants are written to a private temporary file, fsynced and atomically
replaced; the containing directory is fsynced. Tokens are returned only after
successful persistence. A failure before atomic replacement clears memory and preserves the old
file. A failure after replacement or directory fsync has an uncertain persistence
outcome. Spotify may already have invalidated its old refresh token; deliberate
reconnection may then be required. Temporary operation files are cleaned up;
existing grants are not deleted. Callers own and close the injected HTTP client.

Token expiry uses a monotonic clock with a 30-second safety margin. Invalid grants,
grant rejection and broadened scopes clear memory and fail closed. Rate limits
validate Retry-After with a 1-second minimum and 24-hour cap; malformed, negative
or nonfinite values use a 60-second fallback; temporary/network failures impose bounded backoff. No refresh
or code exchange is automatically retried after an uncertain response. A lost
rotation response can require reconnecting rather than guessing whether the
old grant still works. Restart/reconnection policy is explicit operator work.

## User-run local disconnect

After approving the private path and local disconnect:

```sh
python scripts/connect_spotify.py disconnect \
  --destination "$HOME/.local/share/friday/spotify/credentials.json" \
  --approve-local-disconnect
```

This atomically sets the local grant's enabled flag false and preserves its refresh
token. Active managers reject subsequent token requests and clear cached access.
It performs no Spotify request, remote revocation or credential deletion. Review
this app's Spotify account access separately if remote removal is desired. A
credential disabled by this command cannot be overwritten by the connection
helper. Recovery means deliberately preserving/reviewing the old file and using
a fresh approved private destination; there is no automatic re-enable/delete path.

## Remaining activation work

The authentication code/helper is implemented and mocked-tested. Real consent,
real-account eligibility, remote scope behavior and policy compliance remain
unverified. Approved runtime activation would still need to deliberately wire
one TokenManager into the existing playback service, choose the private path and
control disconnect/cache clearing at the service boundary. This candidate's
configuration gate prevents that activation now. Voice routes, model tools and
Spotify content in model/history context remain excluded. No audio streaming,
synced lyrics, external lyrics provider, deployment, flash or push is included.

Offline verification:27 auth tests plus the existing simulator/privacy suite;
235 backend tests pass. All token responses, grants and listener events were
synthetic, with no real callback listener or Spotify connection.

Independent-review repairs: credential opens are nonblocking until regular-file
validation, malformed URL parsing returns a fixed safe rejection, and the
callback requires exactly one expected Host header. Synthetic regressions include
a mode0600 FIFO under a subprocess timeout, duplicate/conflicting/missing Host
headers, malformed IPv6 URLs, nonfinite/negative/huge Retry-After values, and
refresh recovery after an Infinity response.
