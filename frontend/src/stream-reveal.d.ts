export const STREAM_REVEAL_INITIAL_BUFFER_MS: number;
export const STREAM_REVEAL_MIN_COMMIT_MS: number;
export const STREAM_REVEAL_MAX_LAG_MS: number;
export const STREAM_REVEAL_ANIMATION_MS: number;

export function segmentStreamGraphemes(value: string): string[];

export function streamRevealTransition(options?: {
  displayedContent?: string;
  rawContent?: string;
  queueStartedAt?: number;
  lastCommitAt?: number;
  now?: number;
  force?: boolean;
}): {
  kind: 'reset' | 'settled' | 'wait' | 'append';
  displayedContent: string;
  appendedGraphemes: number;
  settled: boolean;
};

declare module '../stream-reveal.mjs' {
  export const STREAM_REVEAL_INITIAL_BUFFER_MS: number;
  export const STREAM_REVEAL_MIN_COMMIT_MS: number;
  export const STREAM_REVEAL_MAX_LAG_MS: number;
  export const STREAM_REVEAL_ANIMATION_MS: number;
  export function segmentStreamGraphemes(value: string): string[];
  export function streamRevealTransition(options?: {
    displayedContent?: string;
    rawContent?: string;
    queueStartedAt?: number;
    lastCommitAt?: number;
    now?: number;
    force?: boolean;
  }): {
    kind: 'reset' | 'settled' | 'wait' | 'append';
    displayedContent: string;
    appendedGraphemes: number;
    settled: boolean;
  };
}
