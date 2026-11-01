/**
 * The media-label vocabulary — the JS port of `nora/domain/media.py`'s
 * `parse_media_type` / `label_for` / `render_placeholder`, exactly the slice
 * the WhatsApp parser uses for body placeholders (`[voice note]`, `[image]`).
 *
 * The taxonomy is driven off the registered media type, never the filename —
 * sender-supplied names never render. The label set is closed, which is what
 * makes a placeholder's label safe by construction.
 */
const TOP_LEVEL_TYPES = new Set([
  'application', 'audio', 'example', 'font', 'image',
  'message', 'model', 'multipart', 'text', 'video',
]);

const BY_TOP_LEVEL = {
  image: 'image', video: 'video', audio: 'audio', text: 'document',
};

const DOCUMENT_SUBTYPES = new Set([
  'pdf', 'rtf', 'msword', 'epub+zip', 'json', 'xml',
  'vnd.openxmlformats-officedocument.wordprocessingml.document',
  'vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'vnd.openxmlformats-officedocument.presentationml.presentation',
  'vnd.oasis.opendocument.text', 'vnd.oasis.opendocument.spreadsheet',
]);

/** `(top, subtype)` per RFC 6838, or `['', '']` when it is not one. */
export function parseMediaType(raw) {
  if (!raw) return ['', ''];
  const essence = String(raw).split(';', 1)[0].trim().toLowerCase();
  if ((essence.match(/\//g) || []).length !== 1) return ['', ''];
  const [top, subtype] = essence.split('/');
  if (!TOP_LEVEL_TYPES.has(top) || !subtype) return ['', ''];
  return [top, subtype];
}

/** Declared type wins; the WhatsApp ref carries no filename to guess from. */
export function resolveMediaType(declared) {
  const [top, subtype] = parseMediaType(declared);
  return top ? `${top}/${subtype}` : '';
}

/** What KIND of thing this is, in words the group would use. Always a label. */
export function labelFor(ref) {
  if (ref.sticker) return 'sticker';
  const [top, subtype] = parseMediaType(resolveMediaType(ref.media_type));
  if (!top) return 'file';
  if (top === 'audio') return ref.voice_note ? 'voice note' : 'audio';
  if (top === 'application') return DOCUMENT_SUBTYPES.has(subtype) ? 'document' : 'file';
  const label = BY_TOP_LEVEL[top] || 'file';
  if (ref.on_demand && label === 'image') return 'cover image';
  return label;
}

/** `[image]` — the no-handle form; custody handles are the app's stage. */
export function renderPlaceholder(label, handle = '') {
  return handle ? `[${label} ${handle}]` : `[${label}]`;
}
