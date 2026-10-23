import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';

const writerUrl = new URL('../src/wav.js', import.meta.url).href;
const codecUrl = new URL('../src/codec.mjs', import.meta.url).href;

test('WAV output stays in recordings when a frame supplies the filename', () => {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'wav-output-'));
  try {
    fs.mkdirSync(path.join(cwd, 'recordings'));
    const result = spawnSync(process.execPath, ['--input-type=module', '-e', `
      import assert from 'node:assert/strict';
      import fs from 'node:fs';
      import path from 'node:path';
      import { WavWriter } from ${JSON.stringify(writerUrl)};
      import { createFrameParser, encodeFrame } from ${JSON.stringify(codecUrl)};
      const record = createFrameParser((user, pcm) => {
        const writer = new WavWriter(path.resolve('recordings', user + '-sample.wav'));
        writer.write(pcm);
        writer.close();
      });
      record(encodeFrame('sample-user', Buffer.from([1, 2, 3, 4])));
      const wav = fs.readFileSync('recordings/sample-user-sample.wav');
      assert.equal(wav.toString('ascii', 0, 4), 'RIFF');
      assert.equal(wav.readUInt32LE(40), 4);
      assert.throws(() => record(encodeFrame('../outside', Buffer.alloc(4))), /recordings/);
      assert.equal(fs.existsSync('outside-sample.wav'), false);
      fs.mkdirSync('recordings/sub');
      assert.throws(() => record(encodeFrame('sub/nested', Buffer.alloc(4))), /recordings/);
    `], { cwd, encoding: 'utf8' });
    assert.equal(result.status, 0, result.stdout + result.stderr);
  } finally {
    fs.rmSync(cwd, { recursive: true, force: true });
  }
});
