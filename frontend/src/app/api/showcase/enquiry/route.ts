import { NextResponse } from "next/server";

import { API_INTERNAL_URL } from "@/lib/api/config";

/**
 * The enquiry form's only door to the API.
 *
 * A thin proxy, and thin on purpose. The browser posts here, to its own
 * origin, so it never learns the API's internal address and no CORS preflight
 * happens. Validation, rate limiting and lead creation all live on the API —
 * duplicating any of them here would give two places to fix and one to forget.
 *
 * The body is forwarded rather than reconstructed field by field: the schema
 * is the API's to own, and a proxy that knows the field names is a proxy that
 * breaks when a field is added.
 */
export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid request." }, { status: 400 });
  }

  try {
    const response = await fetch(
      `${API_INTERNAL_URL}/api/showcase/v1/enquiries`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        cache: "no-store",
      },
    );

    // Pass the status through: the form distinguishes "sent" from "too many",
    // and flattening everything to 500 would tell a rate-limited visitor that
    // the site is broken.
    const text = await response.text();
    return new NextResponse(text, {
      status: response.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch {
    return NextResponse.json(
      { detail: "Could not reach the service." },
      { status: 502 },
    );
  }
}
