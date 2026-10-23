// Minimal streaming WAV writer: 48 kHz stereo s16le PCM -> RIFF/WAVE file.
// Writes a placeholder header, streams data, patches sizes on close.
//
// SPIKE/VERIFICATION ONLY — production mode (src/service.mjs) never writes
// audio to disk; recording exists so spike.mjs can prove decrypted PCM is
// real, per the privacy posture in design/voice-channel/05-sidecar-receive.md.

import fs from 'node:fs';
import { dirname, resolve } from 'node:path';

export class WavWriter {
  constructor(path, { rate = 48000, channels = 2 } = {}) {
    // The mock spike builds filenames from loopback TCP frame user IDs.
    if (dirname(resolve(path)) !== resolve('recordings')) {
      throw new Error('WAV output must be directly inside recordings');
    }
    this.fd = fs.openSync(path, 'w');
    this.bytesWritten = 0;
    this.rate = rate;
    this.channels = channels;
    this.writeHeader(0);
  }

  writeHeader(dataBytes) {
    const h = Buffer.alloc(44);
    h.write('RIFF', 0);
    h.writeUInt32LE(36 + dataBytes, 4);
    h.write('WAVE', 8);
    h.write('fmt ', 12);
    h.writeUInt32LE(16, 16); // PCM chunk size
    h.writeUInt16LE(1, 20); // PCM format
    h.writeUInt16LE(this.channels, 22);
    h.writeUInt32LE(this.rate, 24);
    h.writeUInt32LE(this.rate * this.channels * 2, 28); // byte rate
    h.writeUInt16LE(this.channels * 2, 32); // block align
    h.writeUInt16LE(16, 34); // bits per sample
    h.write('data', 36);
    h.writeUInt32LE(dataBytes, 40);
    fs.writeSync(this.fd, h, 0, 44, 0);
  }

  write(pcm) {
    fs.writeSync(this.fd, pcm);
    this.bytesWritten += pcm.length;
  }

  close() {
    this.writeHeader(this.bytesWritten);
    fs.closeSync(this.fd);
  }
}
