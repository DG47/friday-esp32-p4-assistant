# Native display text compatibility

The installed Montserrat18/24/32 fonts cover printable ASCII, the degree sign,
one bullet and a small private-use icon set. They do not cover the middle dot,
curly quotes, typographic dashes/ellipsis, accented Latin letters, CJK or emoji.
LVGL's enabled missing-glyph placeholder therefore produces boxes for valid
UTF-8 strings. The old subtitle-only apostrophe conversion left other paths
(titles/cards/buttons/status) untouched. Card snprintf could split UTF-8 too.

The firmware routes labels through the selected font's actual
glyph lookup, excluding LVGL's placeholder fallback. Common punctuation uses
readable equivalents: middle-dot separator -> hyphen separator, smart quotes ->
straight quotes, en dash -> hyphen, em dash -> two hyphens, ellipsis -> three dots,
arrows -> ASCII arrows and nonbreaking spaces -> spaces. Valid supported glyphs
remain unchanged. Other unsupported codepoints appear explicitly as [U+XXXX],
rather than silently disappearing or claiming full multilingual font support.
Malformed UTF-8 becomes a visible question mark. Bounded card text is normalized
before concatenation, and output truncation never emits partial UTF-8 or markers.

Stored Calendar/memory/conversation data, browser display and speech are unchanged.
ASCII memory pagination/newline geometry remains unchanged. This is a practical
readability fix for generated punctuation, not a full international font bundle.
True accented/CJK/emoji rendering needs additional reviewed font assets/fallback.

Verification uses synthetic strings and the actual pinned LVGL9.3 Montserrat
font getters at18/24/32, not browser font substitutes. Native tests cover missing
glyph reproduction, punctuation/calendar labels, supported degree, unsupported
Unicode disclosure, malformed bytes and tiny output capacities. Provider-free backend regressions are included in CI. ESP32-P4 cross-build uses existing toolchain/cached components and a private
isolated configuration with credential-like configuration fields cleared.

Run the host test with the actual LVGL v9.3.0 source:

```sh
python3 scripts/test_native_display.py --lvgl-root firmware/managed_components/lvgl__lvgl
```

CI checks out the pinned public LVGL source separately. The host test supports
macOS and Linux section garbage collection and tests actual Montserrat getters.
Firmware application/flashing requires operator approval. Physical pixels/touch
and representative live labels remain separate verification steps.
