import { describe, it, before, after } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { WavWriter } from '../src/wav.js';

const RECORDINGS_ROOT = path.resolve('recordings');
const TEST_DIR = path.join(RECORDINGS_ROOT, 'test-wav');

describe('WavWriter', () => {
  before(() => {
    // Ensure recordings directory exists
    if (!fs.existsSync(RECORDINGS_ROOT)) {
      fs.mkdirSync(RECORDINGS_ROOT, { recursive: true });
    }
    if (!fs.existsSync(TEST_DIR)) {
      fs.mkdirSync(TEST_DIR, { recursive: true });
    }
  });

  after(() => {
    // Clean up test files
    if (fs.existsSync(TEST_DIR)) {
      const files = fs.readdirSync(TEST_DIR);
      for (const file of files) {
        fs.unlinkSync(path.join(TEST_DIR, file));
      }
      fs.rmdirSync(TEST_DIR);
    }
  });

  describe('basic functionality', () => {
    it('should create a valid WAV file with simple filename', () => {
      const testFile = path.join('test-wav', 'test-basic.wav');
      const writer = new WavWriter(testFile);
      
      // Write some dummy PCM data
      const pcmData = Buffer.alloc(1920); // 20ms of stereo 48kHz s16le
      writer.write(pcmData);
      writer.close();
      
      // Verify file exists
      const fullPath = path.join(RECORDINGS_ROOT, testFile);
      assert.ok(fs.existsSync(fullPath), 'WAV file should exist');
      
      // Verify file has correct size (header is written twice: once at start, once at close)
      const stats = fs.statSync(fullPath);
      assert.ok(stats.size >= 1920, 'File size should be at least the data size');
      
      // Clean up
      fs.unlinkSync(fullPath);
    });

    it('should write correct WAV header', () => {
      const testFile = path.join('test-wav', 'test-header.wav');
      const writer = new WavWriter(testFile, { rate: 48000, channels: 2 });
      writer.close();
      
      const fullPath = path.join(RECORDINGS_ROOT, testFile);
      const buffer = fs.readFileSync(fullPath);
      
      // Check RIFF header
      assert.strictEqual(buffer.toString('ascii', 0, 4), 'RIFF', 'Should have RIFF header');
      assert.strictEqual(buffer.toString('ascii', 8, 12), 'WAVE', 'Should have WAVE format');
      assert.strictEqual(buffer.toString('ascii', 12, 16), 'fmt ', 'Should have fmt chunk');
      assert.strictEqual(buffer.toString('ascii', 36, 40), 'data', 'Should have data chunk');
      
      // Check format details
      assert.strictEqual(buffer.readUInt16LE(20), 1, 'Should be PCM format');
      assert.strictEqual(buffer.readUInt16LE(22), 2, 'Should have 2 channels');
      assert.strictEqual(buffer.readUInt32LE(24), 48000, 'Should have 48kHz sample rate');
      assert.strictEqual(buffer.readUInt16LE(34), 16, 'Should be 16-bit samples');
      
      // Clean up
      fs.unlinkSync(fullPath);
    });
  });

  describe('path traversal vulnerability mitigation', () => {
    it('should reject parent directory traversal with ../', () => {
      assert.throws(
        () => new WavWriter('../etc/passwd'),
        /Invalid file path/,
        'Should reject ../ path traversal'
      );
    });

    it('should reject parent directory traversal with ../../', () => {
      assert.throws(
        () => new WavWriter('../../etc/passwd'),
        /Invalid file path/,
        'Should reject ../../ path traversal'
      );
    });

    it('should reject parent directory traversal in subdirectory', () => {
      assert.throws(
        () => new WavWriter('subdir/../../etc/passwd'),
        /Invalid file path/,
        'Should reject path traversal through subdirectory'
      );
    });

    it('should reject absolute paths on Unix', () => {
      assert.throws(
        () => new WavWriter('/etc/passwd'),
        /Invalid file path/,
        'Should reject absolute Unix path'
      );
    });

    it('should reject absolute paths on Windows', () => {
      // On Unix, backslashes are valid filename characters, so we test with a Unix absolute path
      // The Windows path would be rejected on Windows systems
      if (process.platform === 'win32') {
        assert.throws(
          () => new WavWriter('C:\\Windows\\System32\\config\\sam'),
          /Invalid file path/,
          'Should reject absolute Windows path'
        );
      } else {
        // On Unix, test that we still reject absolute paths
        assert.throws(
          () => new WavWriter('/tmp/test.wav'),
          /Invalid file path/,
          'Should reject absolute path'
        );
      }
    });

    it('should reject path that resolves to parent directory', () => {
      assert.throws(
        () => new WavWriter('foo/../../../etc/passwd'),
        /Invalid file path/,
        'Should reject path that resolves outside recordings root'
      );
    });

    it('should reject path with .. as exact match', () => {
      assert.throws(
        () => new WavWriter('..'),
        /Invalid file path/,
        'Should reject .. as path'
      );
    });

    it('should allow valid subdirectory paths', () => {
      const testFile = path.join('test-wav', 'valid-subdir.wav');
      const writer = new WavWriter(testFile);
      writer.close();
      
      const fullPath = path.join(RECORDINGS_ROOT, testFile);
      assert.ok(fs.existsSync(fullPath), 'Should allow valid subdirectory path');
      
      // Clean up
      fs.unlinkSync(fullPath);
    });

    it('should allow paths with dots in filename', () => {
      const testFile = path.join('test-wav', 'file.with.dots.wav');
      const writer = new WavWriter(testFile);
      writer.close();
      
      const fullPath = path.join(RECORDINGS_ROOT, testFile);
      assert.ok(fs.existsSync(fullPath), 'Should allow dots in filename');
      
      // Clean up
      fs.unlinkSync(fullPath);
    });

    it('should allow deeply nested valid paths', () => {
      const deepDir = path.join(TEST_DIR, 'level1', 'level2', 'level3');
      fs.mkdirSync(deepDir, { recursive: true });
      
      const testFile = path.join('test-wav', 'level1', 'level2', 'level3', 'deep.wav');
      const writer = new WavWriter(testFile);
      writer.close();
      
      const fullPath = path.join(RECORDINGS_ROOT, testFile);
      assert.ok(fs.existsSync(fullPath), 'Should allow deeply nested valid paths');
      
      // Clean up
      fs.unlinkSync(fullPath);
      fs.rmdirSync(path.join(TEST_DIR, 'level1', 'level2', 'level3'));
      fs.rmdirSync(path.join(TEST_DIR, 'level1', 'level2'));
      fs.rmdirSync(path.join(TEST_DIR, 'level1'));
    });
  });

  describe('edge cases', () => {
    it('should handle empty PCM data', () => {
      const testFile = path.join('test-wav', 'empty.wav');
      const writer = new WavWriter(testFile);
      writer.close();
      
      const fullPath = path.join(RECORDINGS_ROOT, testFile);
      const stats = fs.statSync(fullPath);
      assert.strictEqual(stats.size, 44, 'Empty WAV should only have header');
      
      // Clean up
      fs.unlinkSync(fullPath);
    });

    it('should handle multiple writes', () => {
      const testFile = path.join('test-wav', 'multi-write.wav');
      const writer = new WavWriter(testFile);
      
      const chunk1 = Buffer.alloc(960);
      const chunk2 = Buffer.alloc(960);
      const chunk3 = Buffer.alloc(960);
      
      writer.write(chunk1);
      writer.write(chunk2);
      writer.write(chunk3);
      writer.close();
      
      const fullPath = path.join(RECORDINGS_ROOT, testFile);
      const stats = fs.statSync(fullPath);
      assert.ok(stats.size >= 2880, 'Should have at least the data size after multiple writes');
      
      // Clean up
      fs.unlinkSync(fullPath);
    });

    it('should handle custom sample rate and channels', () => {
      const testFile = path.join('test-wav', 'custom-format.wav');
      const writer = new WavWriter(testFile, { rate: 16000, channels: 1 });
      writer.close();
      
      const fullPath = path.join(RECORDINGS_ROOT, testFile);
      const buffer = fs.readFileSync(fullPath);
      
      assert.strictEqual(buffer.readUInt16LE(22), 1, 'Should have 1 channel');
      assert.strictEqual(buffer.readUInt32LE(24), 16000, 'Should have 16kHz sample rate');
      
      // Clean up
      fs.unlinkSync(fullPath);
    });
  });
});
