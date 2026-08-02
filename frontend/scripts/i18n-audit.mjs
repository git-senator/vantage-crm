#!/usr/bin/env node
/**
 * i18n guard — finds hard-coded English that bypasses the translation system.
 *
 * Every user-facing string must go through `t(...)` / `getTranslations`, so the
 * whole UI follows the language toggle (RU / PT / EN). This scanner flags the
 * literals that don't: JSX text nodes and the human-readable attributes
 * (placeholder, aria-label, title, label, description) when they contain an
 * English-looking phrase.
 *
 * Run:  node scripts/i18n-audit.mjs
 * Exit: 0 when clean, 1 when findings — so it can gate CI later.
 *
 * It is a heuristic, not a compiler: a few false positives (a brand name, a
 * format example) are expected and can be whitelisted below.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const ROOTS = ["src/app", "src/components"];
const IGNORE_FILE = /\.(test|spec)\.[tj]sx?$/;
// Lines/strings that are legitimately not translatable UI copy.
const WHITELIST = [
  /oklch\(/, /https?:\/\//, /aria-hidden/, /data-\[/, /className=/,
  /"use client"/, /import /, /eslint/, /@/,
  // format examples / brand-ish placeholders (safe to leave literal)
  /Tanaka Holdings|VIP, Repeat|MLS|Re: |\(415\)|2450000|you@|example\.com/,
];
const ATTR = /(placeholder|aria-label|title|label|description)\s*=\s*"([A-Z][^"]*[a-z][^"]*)"/g;
// JSX text: >Some English words< — at least two ASCII-letter words.
const JSXTEXT = />\s*([A-Z][A-Za-z]+(?:\s+[A-Za-z][A-Za-z'.,!?%-]*){1,})\s*</g;

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    const s = statSync(p);
    if (s.isDirectory()) walk(p, out);
    else if (/\.[tj]sx?$/.test(name) && !IGNORE_FILE.test(name)) out.push(p);
  }
  return out;
}

let findings = 0;
const byFile = new Map();

for (const root of ROOTS) {
  let files;
  try { files = walk(root); } catch { continue; }
  for (const file of files) {
    const text = readFileSync(file, "utf8");
    const lines = text.split("\n");
    lines.forEach((line, i) => {
      if (WHITELIST.some((re) => re.test(line))) return;
      // Skip lines that already call the translator on this string.
      const usesT = /\bt\(|getTranslations|useTranslation/.test(line);
      for (const re of [ATTR, JSXTEXT]) {
        re.lastIndex = 0;
        let m;
        while ((m = re.exec(line))) {
          const phrase = (m[2] ?? m[1]).trim();
          if (phrase.length < 3) continue;
          if (usesT) continue;
          const rel = relative(process.cwd(), file);
          if (!byFile.has(rel)) byFile.set(rel, []);
          byFile.get(rel).push(`${i + 1}: ${phrase}`);
          findings++;
        }
      }
    });
  }
}

const sorted = [...byFile.entries()].sort((a, b) => b[1].length - a[1].length);
for (const [file, hits] of sorted) {
  console.log(`\n${file}  (${hits.length})`);
  for (const h of hits.slice(0, 12)) console.log(`  ${h}`);
  if (hits.length > 12) console.log(`  … +${hits.length - 12} more`);
}
console.log(`\n${findings} hard-coded strings across ${byFile.size} files.`);
process.exit(findings ? 1 : 0);
