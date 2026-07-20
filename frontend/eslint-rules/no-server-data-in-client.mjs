/**
 * ESLint rule: forbid `"use client"` modules from importing server-side data.
 *
 * Background — this is not a hypothetical concern. A pre-Phase-0 audit of this
 * codebase proved that `components/layout/topbar.tsx` imported the entire
 * `notifications` array purely to compute one integer, and that the compiled
 * client bundle therefore contained internal transaction notes and a user's
 * email address. Tree-shaking is per-export, so any export a client component
 * touches ships in full.
 *
 * Server-side authorization cannot prevent this: the component imports the data
 * at build time and hands it to the browser. The boundary has to be enforced
 * where the import happens.
 *
 * `import "server-only"` also catches this at build time and is the
 * authoritative backstop. This rule exists because it reports at the exact
 * import line with an actionable message, which a webpack boundary error does
 * not. Both are intentionally kept.
 *
 * See docs/SECURITY.md §4.
 */

/** @param {string} pattern @param {string} value */
function matches(pattern, value) {
  if (pattern.endsWith("*")) return value.startsWith(pattern.slice(0, -1));
  return pattern === value;
}

/** @type {import("eslint").Rule.RuleModule} */
export const noServerDataInClient = {
  meta: {
    type: "problem",
    docs: {
      description:
        "Disallow importing server-side data modules from client components",
    },
    schema: [
      {
        type: "object",
        properties: {
          restricted: { type: "array", items: { type: "string" } },
        },
        additionalProperties: false,
      },
    ],
    messages: {
      forbidden:
        'Client component may not import "{{source}}". Data modules are server-only — ' +
        "pass the specific values this component needs as props from a server " +
        "component instead. Importing the module ships every export it touches to " +
        "the browser. See docs/SECURITY.md §4.",
    },
  },

  create(context) {
    const options = context.options[0] ?? {};
    const restricted = options.restricted ?? [];
    let isClientComponent = false;

    return {
      Program(node) {
        // Directive prologue: leading string-literal ExpressionStatements.
        for (const statement of node.body) {
          if (
            statement.type !== "ExpressionStatement" ||
            statement.expression.type !== "Literal" ||
            typeof statement.expression.value !== "string"
          ) {
            break;
          }
          if (statement.expression.value === "use client") {
            isClientComponent = true;
            break;
          }
        }
      },

      ImportDeclaration(node) {
        if (!isClientComponent) return;
        const source = node.source.value;
        if (typeof source !== "string") return;
        if (!restricted.some((pattern) => matches(pattern, source))) return;

        context.report({ node, messageId: "forbidden", data: { source } });
      },
    };
  },
};

const plugin = {
  rules: { "no-server-data-in-client": noServerDataInClient },
};

export default plugin;
