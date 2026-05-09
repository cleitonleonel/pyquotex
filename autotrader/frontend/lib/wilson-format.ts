/**
 * Confidence-band classifier used by the channel leaderboard. Returns
 * a 3-state badge label: "solid" if we have enough samples and the CI
 * is meaningfully above 0.5, "thin n" if too few samples to trust, or
 * "unsure" otherwise.
 */

export type ConfidenceBand = "solid" | "thin" | "unsure";

export function classifyConfidence(
  n: number,
  ciLow: number | null,
  ciHigh: number | null,
): ConfidenceBand {
  if (n < 10) return "thin";
  if (ciLow === null || ciHigh === null) return "unsure";
  if (ciLow > 0.5) return "solid";
  return "unsure";
}

export function formatWinRate(wr: number | null): string {
  if (wr === null) return "—";
  return `${Math.round(wr * 100)}%`;
}

export function formatPnl(pnl: number): string {
  return `${pnl >= 0 ? "+" : "−"}$${Math.abs(pnl).toFixed(2)}`;
}
