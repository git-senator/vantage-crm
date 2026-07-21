/**
 * Filter object -> query string.
 *
 * Extracted when Properties became the third entity to need it. Shared because
 * the omission rules are a contract with the backend, not a formatting
 * preference: an empty string must be dropped rather than sent, because
 * `?status=` is a validation error server-side while an absent `status` means
 * "no filter".
 *
 * `false` and `0` are deliberately kept — they are meaningful filter values
 * (`min_bedrooms=0`), and a truthiness check would silently discard them.
 *
 * Types only at the boundary, so this is safe to import from client and server
 * modules alike.
 */
// `object`, not `Record<string, unknown>`: the filter types are interfaces,
// and an interface has no implicit index signature, so it does not satisfy
// Record. Widening here beats adding `[key: string]: unknown` to every filter
// interface, which would disable excess-property checking on all of them.
export function toQuery(filters: object): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}
