# Weather touch access candidate

This candidate adds **Weather** to the hold menu. Hold the light to open local
views, then tap Weather: both configured ZIP labels appear with temperature,
wind and humidity. The action is `scene.action: weather`, routed directly to the
existing weather read tool. No voice, speech synthesis, model inference or new
credentials are needed for touch access.

The existing Memory, Reminders, Calendar and Music tiles retain their order.
They occupy two rows of 300 × 144 pixel tiles; Weather occupies a 624 × 104 pixel
row beneath them. Tiles have 24 pixel gaps and sit within the 720 × 720 screen.
The Weather tile uses a native sun icon and the existing palette/fonts. No
technical metadata is added. Other scenes retain their four-card/four-action
limits; only the exact local launcher may contain five actions.

## Ownership and recovery

The backend checks pending confirmation, memory review, busy interaction,
reminder and protected ownership before starting Weather and again before
publishing its result. A Weather tap never confirms/cancels a pending action.
Pending confirmation is restored with the existing bound Cancel request action;
other protected/busy states keep their existing content and show an actionable
notice. A protected request created during an awaited fetch wins over weather.

A per-device ownership token is shared by authenticated peers. New scene actions,
media resume, activation and utterances supersede an older touch Weather request.
Weather progress, result and protected-state notices publish only while their
owner remains current. A per-device lock serializes ownership claims with
publication, closing the check-to-send race. Directed notices use the hub's
bounded serialized peer-send path, including timeout while acquiring the peer
send lock; timeout/disconnect removes the failed peer and releases the navigation
lock so newer actions can claim ownership. The blocked-peer regression checks a stalled send,
a disconnect, and blocked send-lock acquisition. State is retired when the last peer
disconnects. The authenticated two-peer regression holds the old fetch, completes
a newer Dismiss/menu/Reminders/Calendar/Music/Memory/Weather action, then releases
the old fetch and verifies that neither late Weather progress nor result appears.

Repeated reads reuse the existing independent location caches. Failed or
unconfigured weather remains honest; re-open retries. A disconnected native
send leaves menu/review content intact, shows a temporary connection notice,
and allows retry. Dismiss returns to the existing ambient or source-owned view
behavior; holding the light reopens local views.

## Scope and rollout gate

Production changes are limited to launcher action/schema capacity, the direct
weather scene route and ownership checks, safe diagnostic categorization, and
`firmware/main/friday_ui.c` fifth-button geometry/rendering/notices. The existing
native WebSocket sender transmits Weather without a production sender change.
Weather provider configuration, ZIP-area coordinates, cache policy and speech
code are unchanged.

A backend update/restart and firmware app flash are required to make this tile
available on the physical device. **This candidate has not been deployed or
flashed.** A full ESP-IDF build uses isolated copies of the existing private CA,
sdkconfig and pinned managed dependencies. Private build inputs remain unstaged;
no firmware credentials, CA, SDK settings or NVS changes are part of the commit.

After independent review and explicit rollout approval:

1. Back up current source/config and firmware rollback assets; preserve existing
   private CA, sdkconfig, database, LaunchAgent and ZIP weather opt-in.
2. Apply the reviewed backend change and restart the existing service; verify
   strict-TLS readiness and authenticated device reconnect.
3. Use the reviewed UI code and preserved private build configuration. Flash only
   the firmware app through the established procedure, preserving NVS/identity.
4. Verify hold → Weather, both ZIPs, dismiss/reopen and existing local views on
   hardware. Keep spoken/voice validation separate.
5. Push/review/merge and observe exact-SHA CI only when that rollout is authorized.

No screenshot Library upload is included or authorized by this work.
