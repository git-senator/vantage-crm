/**
 * Commission rate: percent on screen, fraction in the database.
 *
 * The column stores 0…1 with four decimal places, which is the right thing to
 * store and the wrong thing to type. An agent thinks "five percent", and the
 * gap between 0.05 and 0.005 is one keystroke and a factor of ten — invisible
 * in a narrow field, and worth thousands on a cheque.
 *
 * Both conversions move the decimal point through the string rather than
 * dividing, for the reason the deal form already states: a rate must not pass
 * through a JS number, because 7.25 / 100 is 0.07250000000000001.
 */

/** Digits and an optional point, which is all a rate may be. */
const NUMERIC = /^(\d*)(?:[.,](\d*))?$/;

function shift(text: string, places: number): string | null {
  const match = NUMERIC.exec(text.trim());
  if (!match) return null;
  const whole = match[1] ?? "";
  const frac = match[2] ?? "";
  if (whole === "" && frac === "") return null;

  const digits = whole + frac;
  // Where the point sits, counted from the right, after the shift.
  const fromRight = frac.length + places;
  if (fromRight <= 0) {
    return (digits + "0".repeat(-fromRight)).replace(/^0+(?=\d)/, "") || "0";
  }
  const padded = digits.padStart(fromRight + 1, "0");
  const cut = padded.length - fromRight;
  const result = `${padded.slice(0, cut)}.${padded.slice(cut)}`;
  // Trailing zeros carry no meaning here and make the field look edited.
  return result.replace(/0+$/, "").replace(/\.$/, "").replace(/^0+(?=\d)/, "") || "0";
}

/** "5" → "0.05", "2.5" → "0.025". Null when it is not a plain number. */
export function percentToRate(percent: string): string | null {
  const text = percent.trim();
  if (text === "") return null;
  return shift(text, 2) ?? text; // unparseable: hand it over and let the server say so
}

/** "0.05" → "5", "0.025" → "2.5". Empty when there is nothing to show. */
export function rateToPercent(rate: string | number | null | undefined): string {
  if (rate === null || rate === undefined) return "";
  const text = String(rate).trim();
  if (text === "") return "";
  return shift(text, -2) ?? "";
}
