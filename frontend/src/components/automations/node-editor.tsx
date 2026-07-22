"use client";

import { Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import type {
  Registries,
  RegistryField,
  WorkflowComparison,
  WorkflowDefinition,
  WorkflowNode,
} from "@/lib/api/types";

/**
 * The inspector: configures whichever node is selected.
 *
 * Every form control is generated from the **registry the server sent**, not
 * from a switch statement over action keys. Adding an action backend-side makes
 * it appear here with its fields already rendered — which is the entire reason
 * the registry is served rather than duplicated, and the only way the palette
 * cannot offer something the engine refuses.
 */
export function NodeEditor({
  nodeId,
  node,
  definition,
  registries,
  triggerEntityType,
  readOnly,
  onChange,
}: {
  nodeId: string;
  node: WorkflowNode;
  definition: WorkflowDefinition;
  registries: Registries;
  triggerEntityType: string | null;
  readOnly: boolean;
  onChange: (node: WorkflowNode) => void;
}) {
  const action = registries.actions.find((entry) => entry.key === node.action);

  // Actions that cannot run under this trigger are hidden rather than shown and
  // rejected: offering "change deal stage" on a note trigger only to fail
  // validation later is a worse experience than never offering it.
  const available = registries.actions.filter(
    (entry) =>
      entry.entity_types.length === 0 ||
      !triggerEntityType ||
      entry.entity_types.includes(triggerEntityType),
  );

  const set = (patch: Partial<WorkflowNode>) => onChange({ ...node, ...patch });
  const setConfig = (key: string, value: unknown) =>
    set({ config: { ...(node.config ?? {}), [key]: value } });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">
          {node.type === "action"
            ? "Action"
            : node.type === "condition"
              ? "Condition"
              : "Delay"}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <Field label="Step name">
          <Input
            value={node.label ?? ""}
            disabled={readOnly}
            onChange={(event) => set({ label: event.target.value })}
            placeholder="What this step does"
          />
        </Field>

        {node.type === "action" && (
          <>
            <Field label="Do what">
              <select
                className="h-9 w-full rounded-md border bg-transparent px-2 text-sm"
                value={node.action ?? ""}
                disabled={readOnly}
                onChange={(event) =>
                  // Config is cleared on change: the previous action's settings
                  // are meaningless to the new one, and keeping them would send
                  // unknown keys the server rejects as a disagreement.
                  set({ action: event.target.value || null, config: {} })
                }
              >
                <option value="">Choose an action…</option>
                {available.map((entry) => (
                  <option key={entry.key} value={entry.key}>
                    {entry.category} · {entry.label}
                  </option>
                ))}
              </select>
            </Field>

            {action?.description && (
              <p className="text-xs text-muted-foreground">{action.description}</p>
            )}

            {action?.fields.map((field) => (
              <ConfigField
                key={field.key}
                field={field}
                value={(node.config ?? {})[field.key]}
                readOnly={readOnly}
                onChange={(value) => setConfig(field.key, value)}
              />
            ))}

            {action && (
              <div className="flex items-center justify-between gap-3 border-t pt-3">
                <Label className="text-xs font-normal">
                  Keep going if this step fails
                </Label>
                <Switch
                  checked={Boolean((node.config ?? {}).continue_on_error)}
                  disabled={readOnly}
                  onCheckedChange={(value) =>
                    setConfig("continue_on_error", Boolean(value))
                  }
                />
              </div>
            )}
          </>
        )}

        {node.type === "delay" && (
          <>
            <Field label="Wait (minutes)">
              <Input
                type="number"
                min={1}
                value={Number((node.config ?? {}).minutes ?? 60)}
                disabled={readOnly}
                onChange={(event) =>
                  setConfig("minutes", Number(event.target.value) || 1)
                }
              />
            </Field>
            <div className="flex items-center justify-between gap-3">
              <div>
                <Label className="text-xs font-normal">
                  Only count working hours
                </Label>
                <p className="text-[11px] text-muted-foreground">
                  A two-hour wait starting at 5pm resumes at 10am.
                </p>
              </div>
              <Switch
                checked={Boolean((node.config ?? {}).business_hours)}
                disabled={readOnly}
                onCheckedChange={(value) =>
                  setConfig("business_hours", Boolean(value))
                }
              />
            </div>
          </>
        )}

        {node.type === "condition" && (
          <ConditionEditor
            node={node}
            registries={registries}
            readOnly={readOnly}
            onChange={onChange}
          />
        )}

        <BranchPicker
          nodeId={nodeId}
          node={node}
          definition={definition}
          readOnly={readOnly}
          onChange={onChange}
        />
      </CardContent>
    </Card>
  );
}

function Field({
  label,
  help,
  children,
}: {
  label: string;
  help?: string | null;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <Label className="text-xs">{label}</Label>
      {children}
      {help && <p className="text-[11px] text-muted-foreground">{help}</p>}
    </div>
  );
}

function ConfigField({
  field,
  value,
  readOnly,
  onChange,
}: {
  field: RegistryField;
  value: unknown;
  readOnly: boolean;
  onChange: (value: unknown) => void;
}) {
  const label = field.required ? field.label : `${field.label} (optional)`;

  if (field.kind === "select") {
    return (
      <Field label={label} help={field.help_text}>
        <select
          className="h-9 w-full rounded-md border bg-transparent px-2 text-sm"
          value={String(value ?? field.default ?? "")}
          disabled={readOnly}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="">Choose…</option>
          {field.options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </Field>
    );
  }

  if (field.kind === "boolean") {
    return (
      <div className="flex items-center justify-between gap-3">
        <Label className="text-xs font-normal">{label}</Label>
        <Switch
          checked={Boolean(value)}
          disabled={readOnly}
          onCheckedChange={(next) => onChange(Boolean(next))}
        />
      </div>
    );
  }

  if (field.kind === "number" || field.kind === "duration") {
    return (
      <Field label={label} help={field.help_text}>
        <Input
          type="number"
          value={value === undefined || value === null ? "" : Number(value)}
          disabled={readOnly}
          onChange={(event) =>
            onChange(event.target.value === "" ? null : Number(event.target.value))
          }
        />
      </Field>
    );
  }

  if (field.kind === "textarea") {
    return (
      <Field label={label} help={field.help_text}>
        <Textarea
          rows={4}
          value={String(value ?? "")}
          disabled={readOnly}
          onChange={(event) => onChange(event.target.value)}
        />
      </Field>
    );
  }

  return (
    <Field
      label={label}
      help={
        field.kind === "template"
          ? (field.help_text ?? "Use {{record.first_name}} to insert values.")
          : field.help_text
      }
    >
      <Input
        value={String(value ?? "")}
        disabled={readOnly}
        onChange={(event) => onChange(event.target.value)}
      />
    </Field>
  );
}

function ConditionEditor({
  node,
  registries,
  readOnly,
  onChange,
}: {
  node: WorkflowNode;
  registries: Registries;
  readOnly: boolean;
  onChange: (node: WorkflowNode) => void;
}) {
  const comparisons = node.comparisons ?? [];

  const setComparison = (index: number, patch: Partial<WorkflowComparison>) =>
    onChange({
      ...node,
      comparisons: comparisons.map((item, position) =>
        position === index ? { ...item, ...patch } : item,
      ),
    });

  return (
    <div className="space-y-3 border-t pt-3">
      <Field label="Match">
        <select
          className="h-9 w-full rounded-md border bg-transparent px-2 text-sm"
          value={node.mode ?? "all"}
          disabled={readOnly}
          onChange={(event) =>
            onChange({ ...node, mode: event.target.value as "all" | "any" })
          }
        >
          <option value="all">All of these</option>
          <option value="any">Any of these</option>
        </select>
      </Field>

      {comparisons.map((comparison, index) => {
        const operator = registries.operators.find(
          (entry) => entry.key === comparison.operator,
        );
        return (
          <div key={index} className="space-y-1.5 rounded-lg border p-2">
            <Input
              value={comparison.field}
              disabled={readOnly}
              placeholder="Field, e.g. stage"
              onChange={(event) =>
                setComparison(index, { field: event.target.value })
              }
            />
            <select
              className="h-9 w-full rounded-md border bg-transparent px-2 text-sm"
              value={comparison.operator}
              disabled={readOnly}
              onChange={(event) =>
                setComparison(index, { operator: event.target.value })
              }
            >
              <option value="">Choose a comparison…</option>
              {registries.operators.map((entry) => (
                <option key={entry.key} value={entry.key}>
                  {entry.category} · {entry.label}
                </option>
              ))}
            </select>
            {/* Unary operators take no value; showing an input for them
                invites somebody to fill it in and wonder why it is ignored. */}
            {operator?.takes_value !== false && (
              <Input
                value={String(comparison.value ?? "")}
                disabled={readOnly}
                placeholder="Value"
                onChange={(event) =>
                  setComparison(index, { value: event.target.value })
                }
              />
            )}
            {!readOnly && (
              <Button
                variant="ghost"
                size="sm"
                className="text-destructive"
                onClick={() =>
                  onChange({
                    ...node,
                    comparisons: comparisons.filter((_, p) => p !== index),
                  })
                }
              >
                <Trash2 className="size-3.5" />
                Remove
              </Button>
            )}
          </div>
        );
      })}

      {!readOnly && (
        <Button
          variant="outline"
          size="sm"
          onClick={() =>
            onChange({
              ...node,
              comparisons: [
                ...comparisons,
                { field: "", operator: "equals", value: "" },
              ],
            })
          }
        >
          <Plus className="size-3.5" />
          Add a condition
        </Button>
      )}
    </div>
  );
}

/**
 * Which step comes next.
 *
 * Explicit rather than drag-to-connect, because the model allows exactly one
 * outgoing edge (two for a condition) — a dropdown makes that constraint
 * obvious, where a canvas would let somebody draw an edge the engine refuses.
 */
function BranchPicker({
  nodeId,
  node,
  definition,
  readOnly,
  onChange,
}: {
  nodeId: string;
  node: WorkflowNode;
  definition: WorkflowDefinition;
  readOnly: boolean;
  onChange: (node: WorkflowNode) => void;
}) {
  const options = Object.entries(definition.nodes ?? {}).filter(
    ([id]) => id !== nodeId,
  );

  const picker = (
    label: string,
    value: string | null | undefined,
    key: "next" | "on_true" | "on_false",
  ) => (
    <Field label={label} key={key}>
      <select
        className="h-9 w-full rounded-md border bg-transparent px-2 text-sm"
        value={value ?? ""}
        disabled={readOnly}
        onChange={(event) => onChange({ ...node, [key]: event.target.value || null })}
      >
        <option value="">Stop here</option>
        {options.map(([id, other]) => (
          <option key={id} value={id}>
            {other.label || id}
          </option>
        ))}
      </select>
    </Field>
  );

  return (
    <div className="space-y-3 border-t pt-3">
      {node.type === "condition" ? (
        <>
          {picker("If yes, go to", node.on_true, "on_true")}
          {picker("If no, go to", node.on_false, "on_false")}
        </>
      ) : (
        picker("Then go to", node.next, "next")
      )}
    </div>
  );
}
