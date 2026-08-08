import type { TranslateFn } from "@/i18n/translate";
import type { Property, RentPeriod } from "@/lib/api/types";
import { formatCurrency } from "@/lib/format";

/**
 * A listing's price, worded for a reader.
 *
 * The number alone is not the price: 4 900 is a monthly rent on one listing and
 * a rounding error on another. A rental therefore always carries its period,
 * and a listing with no price says so in words rather than showing nothing or,
 * worse, a zero.
 *
 * Lives here rather than in `format.ts` because it needs a translator, and
 * `format.ts` is deliberately free of anything that has to be localised beyond
 * number shape.
 */
export function listingPrice(
  property: Pick<Property, "price" | "rent_period">,
  t: TranslateFn,
): string {
  if (!property.price) return t("body.dvOnApplication");
  const amount = formatCurrency(Number(property.price));
  if (!property.rent_period) return amount;
  return `${amount}${t(periodKey(property.rent_period))}`;
}

function periodKey(period: RentPeriod): string {
  switch (period) {
    case "week":
      return "body.perWeek";
    case "day":
      return "body.perDay";
    default:
      return "body.perMonth";
  }
}
