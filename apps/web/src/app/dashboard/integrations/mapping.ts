const MAX_CODES = 200;

/**
 * "73761001 = COLONOSCOPY" on each line: the EHR's service code, then our procedure type code.
 * Returns the mapping, or a message saying what to fix.
 */
export function parseServiceCodes(
  value: string,
): Record<string, string> | string {
  const codes: Record<string, string> = {};
  const lines = value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length > MAX_CODES) {
    return `Map at most ${String(MAX_CODES)} codes.`;
  }
  for (const line of lines) {
    const [ehr, ours, ...rest] = line.split("=").map((part) => part.trim());
    if (!ehr || !ours || rest.length > 0) {
      return `Write each line as "EHR code = procedure code" (not "${line}").`;
    }
    codes[ehr] = ours.toUpperCase();
  }
  return codes;
}

/** The mapping back as editable lines, for the form. */
export function formatServiceCodes(
  codes: Record<string, string> | undefined,
): string {
  return Object.entries(codes ?? {})
    .map(([ehr, ours]) => `${ehr} = ${ours}`)
    .join("\n");
}
