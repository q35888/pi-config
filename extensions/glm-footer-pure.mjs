// Pure formatting helpers — no IO, no pi deps. Shared between the .ts extension and tests.
export function formatTokens(n) {
  if (!n || n <= 0) return '0';
  if (n < 1e3) return String(Math.round(n));
  const strip = (x) => { const s = x.toFixed(1); return s.endsWith('.0') ? s.slice(0, -2) : s; };
  if (n < 1e6) return strip(n / 1e3) + 'K';
  if (n < 1e9) return strip(n / 1e6) + 'M';
  return strip(n / 1e9) + 'B';
}

export function makeBar(percent, width) {
  width = width || 10;
  const p = Math.max(0, Math.min(100, percent || 0));
  const filled = Math.round(p * width / 100);
  return '━'.repeat(filled) + '─'.repeat(width - filled);
}

export function remainingPct(usedPct) {
  return Math.max(0, Math.min(100, 100 - (usedPct || 0)));
}

export function baseModelName(name) {
  if (!name) return 'GLM';
  return name.replace(/\[\d+(?:\.\d+)?[km]\]/i, '').trim() || name;
}

export function colorKey(usedPct) {
  if (usedPct < 50) return 'success';
  if (usedPct < 80) return 'warning';
  return 'error';
}

export function sumSegments(parts, sep) {
  sep = sep || '  ';
  return parts.filter((p) => p != null && p !== '').join(sep);
}
