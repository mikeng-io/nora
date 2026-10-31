/**
 * Python-compatible canonical JSON — byte-for-byte `json.dumps(value,
 * sort_keys=True, separators=(",", ":"))`, which is what the shared
 * `nora.platform.bridge` request_hash signs.
 *
 * The differences from JSON.stringify are small and every one is load-bearing:
 * sort_keys at every depth, no spaces, and ensure_ascii=True escaping — every
 * non-ASCII code unit becomes \uXXXX (lowercase hex), so "👍" is the surrogate
 * pair "\ud83d\udc4d" and "你好" is "你好". JS strings are UTF-16, so
 * walking code units reproduces CPython's BMP-by-BMP escaping exactly.
 * sha256() of a UTF-8 body that kept the emoji would hash a different string
 * than the Python bridges compute for the identical action.
 *
 * Numbers: integers and non-fractional values serialize like Python ints;
 * fractional values use Python's repr shortest form (JS Number formatting
 * agrees on every non-integral double). A value that is not finite, or is
 * bigint/function/undefined/symbol at a serialized position, raises — an
 * action request the Python side could not hash identically must not be
 * hashed differently here.
 */

function escString(s) {
  let out = '"';
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c === 0x22) out += '\\"';
    else if (c === 0x5c) out += '\\\\';
    else if (c === 0x08) out += '\\b';
    else if (c === 0x09) out += '\\t';
    else if (c === 0x0a) out += '\\n';
    else if (c === 0x0c) out += '\\f';
    else if (c === 0x0d) out += '\\r';
    else if (c < 0x20 || c >= 0x7f) out += `\\u${c.toString(16).padStart(4, '0')}`;
    else out += s[i];
  }
  return `${out}"`;
}

function num(n) {
  if (!Number.isFinite(n)) throw new TypeError('non-finite number is not canonical');
  if (Number.isInteger(n)) {
    // Python ints have arbitrary precision; past 2^53 a JS number has already
    // lost the digits, so refuse instead of hashing a lie.
    if (Math.abs(n) > Number.MAX_SAFE_INTEGER) {
      throw new TypeError('integer exceeds safe range; canonical hash would differ');
    }
    return String(n);
  }
  return String(n);
}

function canon(value) {
  if (value === null) return 'null';
  switch (typeof value) {
    case 'boolean': return value ? 'true' : 'false';
    case 'string': return escString(value);
    case 'number': return num(value);
    case 'object': break;
    default:
      throw new TypeError(`cannot canonically serialize ${typeof value}`);
  }
  if (Array.isArray(value)) {
    return `[${value.map(canon).join(',')}]`;
  }
  // Plain object: sort keys like Python's sort_keys (UTF-16 code-unit order
  // agrees with code-point order for every key the contract produces).
  const keys = Object.keys(value).sort();
  const parts = [];
  for (const k of keys) {
    const v = value[k];
    if (v === undefined || typeof v === 'function' || typeof v === 'symbol') {
      throw new TypeError('unserializable object member in canonical JSON');
    }
    parts.push(`${escString(k)}:${canon(v)}`);
  }
  return `{${parts.join(',')}}`;
}

export function canonicalJson(value) {
  return canon(value);
}
