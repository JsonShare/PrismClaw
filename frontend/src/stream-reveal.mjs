export const STREAM_REVEAL_INITIAL_BUFFER_MS = 36;
export const STREAM_REVEAL_MIN_COMMIT_MS = 24;
export const STREAM_REVEAL_MAX_LAG_MS = 150;
export const STREAM_REVEAL_ANIMATION_MS = 110;

export function segmentStreamGraphemes(value) {
  const text = String(value || '');
  if (!text) return [];
  if (typeof Intl !== 'undefined' && typeof Intl.Segmenter === 'function') {
    const segmenter = new Intl.Segmenter(undefined, { granularity: 'grapheme' });
    return Array.from(segmenter.segment(text), (entry) => entry.segment);
  }
  return Array.from(text);
}

export function streamRevealTransition({
  displayedContent = '',
  rawContent = '',
  queueStartedAt = 0,
  lastCommitAt = 0,
  now = 0,
  force = false,
} = {}) {
  const displayed = String(displayedContent || '');
  const raw = String(rawContent || '');
  const currentTime = Math.max(0, Number(now) || 0);
  if (!raw.startsWith(displayed)) {
    return {
      kind: 'reset',
      displayedContent: raw,
      appendedGraphemes: 0,
      settled: true,
    };
  }
  if (raw === displayed) {
    return {
      kind: 'settled',
      displayedContent: displayed,
      appendedGraphemes: 0,
      settled: true,
    };
  }
  const queuedAt = Math.max(0, Number(queueStartedAt) || currentTime);
  const age = Math.max(0, currentTime - queuedAt);
  const sinceCommit = Math.max(0, currentTime - (Number(lastCommitAt) || 0));
  if (!force && age < STREAM_REVEAL_INITIAL_BUFFER_MS) {
    return { kind: 'wait', displayedContent: displayed, appendedGraphemes: 0, settled: false };
  }
  if (!force && lastCommitAt && sinceCommit < STREAM_REVEAL_MIN_COMMIT_MS) {
    return { kind: 'wait', displayedContent: displayed, appendedGraphemes: 0, settled: false };
  }
  const backlog = segmentStreamGraphemes(raw.slice(displayed.length));
  if (!force && backlog.length === 1 && age < STREAM_REVEAL_INITIAL_BUFFER_MS * 2) {
    return { kind: 'wait', displayedContent: displayed, appendedGraphemes: 0, settled: false };
  }
  const remainingMs = Math.max(0, STREAM_REVEAL_MAX_LAG_MS - age);
  const remainingCommits = force ? 1 : Math.max(1, Math.ceil(remainingMs / STREAM_REVEAL_MIN_COMMIT_MS));
  const count = force ? backlog.length : Math.max(1, Math.ceil(backlog.length / remainingCommits));
  const appended = backlog.slice(0, count).join('');
  const nextContent = `${displayed}${appended}`;
  return {
    kind: 'append',
    displayedContent: nextContent,
    appendedGraphemes: count,
    settled: nextContent === raw,
  };
}
