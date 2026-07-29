/**
 * The pure translation resolver.
 *
 * No React, no I/O — just "given a dictionary, a fallback dictionary and a
 * dotted key, produce a string". Keeping it isolated is what makes it unit
 * testable and lets both the client hook and the server helper share one code
 * path with identical fallback and interpolation semantics.
 */

import type { Dictionary } from "./locales/en";

/** A nested dictionary node: strings all the way down. */
export type DictNode = { [key: string]: string | DictNode };

/** Values interpolated into `{placeholder}` slots. */
export type TranslateParams = Record<string, string | number>;

/** Walk a dotted path into a dictionary; return the leaf string or undefined. */
function lookup(dict: DictNode, key: string): string | undefined {
  let node: string | DictNode | undefined = dict;
  for (const segment of key.split(".")) {
    if (typeof node !== "object" || node === null) return undefined;
    node = node[segment];
  }
  return typeof node === "string" ? node : undefined;
}

/** Replace every `{name}` in `template` with the matching param, if present. */
function interpolate(template: string, params?: TranslateParams): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) => {
    const value = params[name];
    return value === undefined ? match : String(value);
  });
}

/**
 * Resolve `key` against `dict`, falling back to `fallback`, then to the key.
 *
 * The fallback chain is the reason a half-finished translation never shows a
 * blank screen: a key missing from Portuguese renders its English text, and a
 * key missing from both renders the key itself (which is visible in the UI and
 * so gets noticed and fixed, rather than silently disappearing).
 */
export function translate(
  dict: DictNode,
  fallback: DictNode,
  key: string,
  params?: TranslateParams,
): string {
  const value = lookup(dict, key) ?? lookup(fallback, key);
  return value === undefined ? key : interpolate(value, params);
}

/** A bound translator over a resolved dictionary. */
export type TranslateFn = (key: string, params?: TranslateParams) => string;

/** Bind a dictionary (and its fallback) into a ready-to-call `t`. */
export function createTranslator(
  dict: Dictionary,
  fallback: Dictionary,
): TranslateFn {
  return (key, params) =>
    translate(dict as DictNode, fallback as DictNode, key, params);
}
