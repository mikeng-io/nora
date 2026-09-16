// Tiny local consumer for the wake-gate seam: stands in for the future
// Python wake gate so the forward path can be verified without it.
//
//   node consume.mjs                  listen on TCP (default 127.0.0.1:4599)
//                                     and print per-user stats until Ctrl-C.
//   node consume.mjs --file <fixture> parse a framed-stream fixture file
//                                     (e.g. one written by a mock run) and
//                                     print stats, then exit.
//
// Stats per user: bytes received, frame count, duration, silence ratio.
// Frames are the pump's framing units; PCM is s16le 48 kHz stereo, so a
// 20 ms Opus frame is 3840 bytes.

import fs from 'node:fs';
import net from 'node:net';
import { createFrameParser, PCM_FRAME_BYTES } from './src/recv.js';

const PORT = Number(process.env.PUMP_PORT ?? 4599);

const perUser = new Map();
function note(userId, pcm) {
  const s = perUser.get(userId) ?? { bytes: 0, frames: 0, silent: 0, samples: 0 };
  s.bytes += pcm.length;
  s.frames += Math.round(pcm.length / PCM_FRAME_BYTES) || 1;
  for (let i = 0; i + 1 < pcm.length; i += 2) {
    s.samples++;
    if (Math.abs(pcm.readInt16LE(i)) < 100) s.silent++;
  }
  perUser.set(userId, s);
}

function report() {
  let any = false;
  for (const [userId, s] of perUser) {
    any = true;
    const seconds = s.bytes / 4 / 48000;
    const silence = s.samples ? (100 * s.silent) / s.samples : 0;
    console.log(
      `user ${userId}: bytes=${s.bytes} frames=${s.frames} ` +
      `duration=${seconds.toFixed(2)}s silence=${silence.toFixed(1)}%`,
    );
  }
  if (!any) console.log('no frames received');
}

const fileIdx = process.argv.indexOf('--file');
if (fileIdx !== -1) {
  const data = fs.readFileSync(process.argv[fileIdx + 1]);
  createFrameParser(note)(data);
  report();
} else {
  const server = net.createServer((conn) => {
    console.log(`[consume] producer connected from ${conn.remoteAddress}`);
    conn.on('data', createFrameParser(note));
  });
  server.listen(PORT, '127.0.0.1', () => {
    console.log(`[consume] listening on 127.0.0.1:${PORT} (Ctrl-C for stats)`);
  });
  setInterval(report, 5000).unref();
  process.on('SIGINT', () => {
    report();
    process.exit(0);
  });
}
