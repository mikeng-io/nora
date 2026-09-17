# DAVE decrypt path — confirmed present in `@discordjs/voice@0.19.2`

Verified by reading the installed bundle
`node_modules/@discordjs/voice/dist/index.js` (line numbers below) after
`npm install` on 2026-09-14. Upstream source file names (discord.js repo,
`packages/voice/src/`) are given for cross-reference; the dist lines are the
authoritative citation for the pinned version.

## The chain, packet by packet

1. **UDP receive + SSRC→userId binding.**
   `dist/index.js:2119-2126` (`VoiceReceiver.onUdpMessage`): reads the SSRC
   from the RTP header, looks it up in `ssrcMap`, and gets the per-user
   subscription stream. The map is populated from the voice-gateway Speaking
   opcode at `dist/index.js:2043-2048` (`VoiceReceiver.onWsPacket` →
   `ssrcMap.update({ userId, audioSSRC })`). Upstream:
   `src/receive/VoiceReceiver.ts`. **Identity is protocol-native**: the
   decrypted packet is attributed to the user Discord says owns that SSRC —
   no speaker recognition anywhere.

2. **Transport decrypt + DAVE E2E decrypt.**
   `dist/index.js:2093-2111` (`VoiceReceiver.parsePacket`):
   - line 2094: `this.decrypt(buffer, mode, nonce, secretKey)` — the
     negotiated transport encryption (`aead_xchacha20_poly1305_rtpsize`),
     with mode/nonce/secret key from the Session Description opcode
     (captured at `dist/index.js:1576`).
   - lines 2107-2110: if the connection's networking state holds a DAVE
     session, `packet = daveSession.decrypt(packet, userId)` — the E2E
     layer, keyed **per userId**.
   The decrypted Opus packet is pushed into the user's stream at
   `dist/index.js:2139`; a parse/decrypt throw destroys only that stream
   (`dist/index.js:2140-2142`, `stream.destroy(error)`) — one user's stream
   dying cannot kill the process. Our `recv.js` resubscribes on the next
   speaking start.

3. **The DAVE session itself.**
   `dist/index.js:948-970` (`DAVESession.decrypt`) calls
   `session.decrypt(userId, MediaType.AUDIO, packet)` on
   `@snazzah/davey@0.1.9` — the Node binding for **Discord's official
   libdave** (MLS). Session creation and MLS group transitions are driven by
   voice-gateway opcodes at `dist/index.js:1441-1456` (`createDaveSession`)
   and `dist/index.js:1607+` (`DavePrepareTransition` / `DaveExecuteTransition`
   / `DavePrepareEpoch`). Upstream: `src/networking/DAVESession.ts`.

## What this scaffolding relies on

`join()` in `src/recv.js` never touches decryption directly: it calls
`connection.receiver.subscribe(userId)`, which returns the stream fed by
step 1-2 above — i.e. **post-transport-decrypt, post-DAVE-decrypt Opus
packets**, already bound to a user. Decoding Opus → PCM is done with
`prism-media`'s `opus.Decoder` (backend `@discordjs/opus@0.10.0`).

## What the live spike still has to prove (the gate)

Package availability is not a working receive path. `spike.mjs --live` is the
operator-run proof: MLS negotiation succeeding in the real guild, SSRC→userId
mapping attaching, and decrypted PCM actually flowing (non-silent,
intelligible WAV per user). DAVE decrypt failures surface as `debug` events
on the session and as destroyed per-user streams (`streamError` in `recv.js`)
— a silent-failure mode to watch for on the first live run is WAVs that are
all zeros, which would mean decrypt is failing, not that nobody spoke.
