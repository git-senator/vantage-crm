import "server-only";

import { API_INTERNAL_URL } from "@/lib/api/config";

/**
 * Reading the public catalogue.
 *
 * Separate from `lib/api/server.ts` on purpose: that client forwards the
 * visitor's access token, and this one must never have a token to forward. The
 * showcase is read by strangers, and the surface it talks to is the only
 * unauthenticated one the API exposes.
 *
 * The fetch happens on the server even though nothing is secret, because the
 * photo URLs are freshly signed and short-lived: rendering them into the HTML
 * as it is served is what makes them work at all.
 */

export interface ShowcasePhoto {
  url: string;
  is_cover: boolean;
}

export interface ShowcaseListing {
  id: string;
  title: string;
  description: string | null;
  features: string[];
  price: string | null;
  currency: string | null;
  property_type: string | null;
  bedrooms: number | null;
  bathrooms: string | null;
  area_m2: number | null;
  city: string | null;
  state: string | null;
  country: string | null;
  cover_url: string | null;
  photo_count: number;
  photos: ShowcasePhoto[];
}

const SHOWCASE_PREFIX = "/api/showcase/v1";

export async function fetchListings(
  locale: string,
  limit = 60,
): Promise<ShowcaseListing[]> {
  const response = await fetch(
    `${API_INTERNAL_URL}${SHOWCASE_PREFIX}/listings?limit=${limit}`,
    {
      headers: { Accept: "application/json", "Accept-Language": locale },
      // Signed photo URLs expire, so a cached page would serve dead images.
      cache: "no-store",
    },
  );
  if (!response.ok) {
    return [];
  }
  return (await response.json()) as ShowcaseListing[];
}

export async function fetchListing(
  id: string,
  locale: string,
): Promise<ShowcaseListing | null> {
  const response = await fetch(
    `${API_INTERNAL_URL}${SHOWCASE_PREFIX}/listings/${id}`,
    {
      headers: { Accept: "application/json", "Accept-Language": locale },
      cache: "no-store",
    },
  );
  if (!response.ok) {
    return null;
  }
  return (await response.json()) as ShowcaseListing;
}
