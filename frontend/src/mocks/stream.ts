/**
 * Turns a finished answer into a plausible token stream.
 *
 * Cadence is deliberately uneven — a variable first-token delay, then chunks
 * that pause slightly longer at sentence boundaries. A perfectly even stream
 * reads as fake, and the composer's stop button needs something real to cancel.
 */
export function chunkAnswer(text: string): string[] {
  return text.match(/\S+\s*/g) ?? [text];
}

export function delayFor(chunk: string, index: number): number {
  if (index === 0) return 220 + Math.random() * 260;
  if (/[.!?]\s*$/.test(chunk)) return 70 + Math.random() * 90;
  if (/[,;:]\s*$/.test(chunk)) return 40 + Math.random() * 40;
  if (/\n\s*$/.test(chunk)) return 60 + Math.random() * 60;
  return 14 + Math.random() * 26;
}

export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
