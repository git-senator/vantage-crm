import { describe, expect, it } from "vitest";

import en from "@/i18n/locales/en";
import ptBR from "@/i18n/locales/pt-BR";
import ru from "@/i18n/locales/ru";
import { createTranslator, translate, type DictNode } from "@/i18n/translate";

const base = en as unknown as DictNode;

describe("translate", () => {
  it("resolves a dotted key against the dictionary", () => {
    expect(translate(base, base, "nav.leads")).toBe("Leads");
    expect(translate(base, base, "buttons.save")).toBe("Save");
  });

  it("interpolates named placeholders", () => {
    expect(translate(base, base, "tables.page", { current: 2, total: 9 })).toBe(
      "Page 2 of 9",
    );
    expect(
      translate(base, base, "settings.seatsUsed", { used: 6, total: 25 }),
    ).toBe("6 of 25 seats used on your plan.");
  });

  it("falls back to the fallback dictionary for a missing key", () => {
    const partial: DictNode = { nav: { leads: "Лиды" } };
    // Present in the partial locale → uses it.
    expect(translate(partial, base, "nav.leads")).toBe("Лиды");
    // Absent from the partial locale → English fallback.
    expect(translate(partial, base, "buttons.save")).toBe("Save");
  });

  it("returns the key itself when it is missing everywhere", () => {
    expect(translate(base, base, "does.not.exist")).toBe("does.not.exist");
  });

  it("leaves an unmatched placeholder in place", () => {
    expect(translate(base, base, "tables.page", { current: 2 })).toBe(
      "Page 2 of {total}",
    );
  });

  it("createTranslator binds a dictionary and its fallback", () => {
    const t = createTranslator(en, en);
    expect(t("common.appTagline")).toBe("AI Real Estate CRM");
  });
});

describe("locale parity", () => {
  // The typechecker already guarantees structural parity; this catches an
  // accidental empty string, which types cannot.
  const keys = (node: DictNode, prefix = ""): string[] =>
    Object.entries(node).flatMap(([key, value]) => {
      const path = prefix ? `${prefix}.${key}` : key;
      return typeof value === "string" ? [path] : keys(value, path);
    });

  const enKeys = keys(base);

  it("every English key is a non-empty string in every locale", () => {
    for (const locale of [ptBR, ru]) {
      const node = locale as unknown as DictNode;
      for (const key of enKeys) {
        expect(translate(node, node, key), `missing: ${key}`).not.toBe(key);
      }
    }
  });
});
