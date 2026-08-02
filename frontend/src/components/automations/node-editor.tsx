"use client";

import { Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useTranslation } from "@/i18n/language-provider";
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
  const { t } = useTranslation();
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
            ? t("body.neAction")
            : node.type === "condition"
              ? t("body.neCondition")
              : t("body.neDelay")}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <Field label={t("body.neStepName")}>
          <Input
            value={node.label ?? ""}
            disabled={readOnly}
            onChange={(event) => set({ label: event.target.value })}
            placeholder={t("body.neStepPlaceholder")}
          />
        </Field>

        {node.type === "action" && (
          <>
            <Field label={t("body.neDoWhat")}>
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
                <option value="">{t("body.neChooseAction")}</option>
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
                  {t("body.neKeepGoing")}
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
            <Field label={t("body.neWaitMinutes")}>
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
                  {t("body.neWorkingHours")}
                </Label>
                <p className="text-[11px] text-muted-foreground">
                  {t("body.neWorkingHoursHint")}
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
  const { t } = useTranslation();
  const label = field.required
    ? field.label
    : t("body.neOptional", { label: field.label });

  if (field.kind === "select") {
    return (
      <Field label={label} help={field.help_text}>
        <select
          className="h-9 w-full rounded-md border bg-transparent px-2 text-sm"
          value={String(value ?? field.default ?? "")}
          disabled={readOnly}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="">{t("body.neChoose")}</option>
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
          ? (field.help_text ?? t("body.neTemplateHint"))
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
  const { t } = useTranslation();
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
      <Field label={t("body.neMatch")}>
        <select
          className="h-9 w-full rounded-md border bg-transparent px-2 text-sm"
          value={node.mode ?? "all"}
          disabled={readOnly}
          onChange={(event) =>
            onChange({ ...node, mode: event.target.value as "all" | "any" })
          }
        >
          <option value="all">{t("body.neAllOfThese")}</option>
          <option value="any">{t("body.neAnyOfThese")}</option>
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
              placeholder={t("body.neFieldPlaceholder")}
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
              <option value="">{t("body.neChooseComparison")}</option>
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
                placeholder={t("body.neValue")}
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
                {t("buttons.remove")}
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
          {t("body.neAddCondition")}
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
  const { t } = useTranslation();
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
        <option value="">{t("body.neStopHere")}</option>
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
          {picker(t("body.neIfYesGoTo"), node.on_true, "on_true")}
          {picker(t("body.neIfNoGoTo"), node.on_false, "on_false")}
        </>
      ) : (
        picker(t("body.neThenGoTo"), node.next, "next")
      )}
    </div>
  );
}
