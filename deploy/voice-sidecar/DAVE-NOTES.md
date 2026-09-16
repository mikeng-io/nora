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
   `@snazzah/davey` — the Node binding for **Discord's official libdave**
   (MLS). This package pins `@snazzah/davey@0.1.12` explicitly (the
   `@discordjs/voice@0.19.2` range `^0.1.9` would otherwise float). Session
   creation and MLS group transitions are driven by voice-gateway opcodes at
   `dist/index.js:1441-1456` (`createDaveSession`) and `dist/index.js:1607+`
   (`DavePrepareTransition` / `DaveExecuteTransition` / `DavePrepareEpoch`).
   Upstream: `src/networking/DAVESession.ts`.

## What this service relies on

`join()` in `src/recv.js` never touches decryption directly: it calls
`connection.receiver.subscribe(userId)`, which returns the stream fed by
step 1-2 above — i.e. **post-transport-decrypt, post-DAVE-decrypt Opus
packets**, already bound to a user. Decoding Opus → PCM is done with
`prism-media`'s `opus.Decoder` (backend `@discordjs/opus@0.10.0`).

## The live spike — PASSED

The stage-2 gate from `design/voice-channel/05-sidecar-receive.md` (a live
DAVE spike in the real guild: MLS negotiation, SSRC→userId mapping,
decrypted PCM actually flowing) was run with `spike.mjs --live` and PASSED:
per-user WAVs were non-silent and intelligible. That prototype run is the
evidence this service is the production shape of. The silent-failure mode to
watch for on any FUTURE live run remains: WAVs (or wake-gate frames) that
are all zeros mean decrypt is failing, not that nobody spoke — DAVE decrypt
failures surface as destroyed per-user streams (`streamError` in `recv.js`).
