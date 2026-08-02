"use client";

import { useRouter } from "next/navigation";
import { useCallback, useMemo, useState } from "react";
import {
  AlertTriangle,
  ArrowDown,
  Clock,
  GitBranch,
  Loader2,
  Plus,
  Trash2,
  Zap,
} from "lucide-react";

import { NodeEditor } from "@/components/automations/node-editor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useTranslation } from "@/i18n/language-provider";
import { ClientApiError } from "@/lib/api/client";
import {
  publishWorkflow,
  saveDefinition,
  validateDefinition,
} from "@/lib/api/automations-client";
import type {
  Registries,
  WorkflowDefinition,
  WorkflowNode,
  WorkflowNodeType,
} from "@/lib/api/types";
import { cn } from "@/lib/utils";

/**
 * The visual workflow builder.
 *
 * Renders the definition as a **vertical chain**, not a free-form canvas with
 * draggable boxes and drawn edges. That is a deliberate match to the data model:
 * the backend accepts a tree — one outgoing edge per node, two for a condition —
 * so a canvas that lets you draw arbitrary edges would let you draw workflows
 * the engine refuses. A chain makes the legal shapes the only expressible ones,
 * and it reads top-to-bottom the way people describe automations out loud.
 *
 * Condition branches render as two indented columns. That is where the shape
 * stops being a line, and it is the only place the layout has to think.
 *
 * State lives here as one `WorkflowDefinition`. Every edit produces a new
 * object rather than mutating, so an accidental shared reference between two
 * nodes cannot make one edit change two steps.
 */
export function WorkflowBuilder({
  workflowId,
  initialDefinition,
  registries,
  canManage,
}: {
  workflowId: string;
  initialDefinition: WorkflowDefinition;
  registries: Registries;
  canManage: boolean;
}) {
  const router = useRouter();
  const { t } = useTranslation();
  const [definition, setDefinition] =
    useState<WorkflowDefinition>(initialDefinition);
  const [selected, setSelected] = useState<string | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);

  const nodes = definition.nodes ?? {};
  const trigger = registries.triggers.find(
    (entry) => entry.key === definition.trigger?.type,
  );

  const update = useCallback((next: WorkflowDefinition) => {
    setDefinition(next);
    setDirty(true);
    setMessage(null);
  }, []);

  /** Depth-first order from the start node, so the chain renders in sequence. */
  const ordered = useMemo(() => buildChain(definition), [definition]);

  async function run(action: () => Promise<unknown>, done?: string) {
    setPending(true);
    setMessage(null);
    try {
      await action();
      if (done) setMessage(done);
    } catch (caught) {
      if (caught instanceof ClientApiError) {
        // The publish endpoint returns every problem in the problem body, so
        // the canvas can mark up in one pass rather than one error at a time.
        const detail = caught.problem as { errors?: string[] } | null;
        setErrors(detail?.errors ?? []);
        setMessage(caught.message);
      } else {
        setMessage(t("body.errGeneric"));
      }
    } finally {
      setPending(false);
    }
  }

  function addNode(type: WorkflowNodeType, afterId: string | null) {
    const id = nextNodeId(nodes);
    const created: WorkflowNode =
      type === "condition"
        ? { type, label: t("body.wbCheckSomething"), mode: "all", comparisons: [], on_true: null, on_false: null }
        : type === "delay"
          ? { type, label: t("body.wbWait"), config: { minutes: 60 }, next: null }
          : { type, label: t("body.wbDoSomething"), action: null, config: {}, next: null };

    const updated: Record<string, WorkflowNode> = { ...nodes, [id]: created };
    let startNode = definition.start_node ?? null;

    if (afterId === null) {
      // Appending to an empty workflow, or inserting a new head.
      created.next = startNode;
      startNode = id;
    } else {
      const parent = nodes[afterId];
      // Splice into the chain: the new node inherits the parent's successor.
      created.next = parent.type === "condition" ? null : (parent.next ?? null);
      updated[afterId] =
        parent.type === "condition"
          ? { ...parent, on_true: parent.on_true ?? id }
          : { ...parent, next: id };
    }

    update({ ...definition, start_node: startNode, nodes: updated });
    setSelected(id);
  }

  function removeNode(id: string) {
    const remaining = { ...nodes };
    const removed = remaining[id];
    delete remaining[id];

    // Re-point anything that referenced it at whatever it pointed to, so
    // deleting a middle step closes the gap instead of orphaning the tail.
    const successor = removed.type === "condition" ? null : (removed.next ?? null);
    for (const [key, node] of Object.entries(remaining)) {
      remaining[key] = {
        ...node,
        next: node.next === id ? successor : node.next,
        on_true: node.on_true === id ? successor : node.on_true,
        on_false: node.on_false === id ? successor : node.on_false,
      };
    }

    update({
      ...definition,
      start_node: definition.start_node === id ? successor : definition.start_node,
      nodes: remaining,
    });
    if (selected === id) setSelected(null);
  }

  return (
    <div className="grid gap-6 xl:grid-cols-[1fr_360px]">
      <div className="min-w-0 space-y-3">
        {/* ------------------------------------------------------ trigger */}
        <Card className="border-primary/40 p-4">
          <div className="flex items-start gap-3">
            <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary">
              <Zap className="size-4" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                {t("body.wbWhen")}
              </p>
              <p className="text-sm font-medium">
                {trigger?.label ?? t("body.wbNoTrigger")}
              </p>
              {trigger?.description && (
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {trigger.description}
                </p>
              )}
            </div>
          </div>
        </Card>

        {ordered.length === 0 ? (
          <Card className="border-dashed p-6 text-center">
            <p className="text-sm text-muted-foreground">
              {t("body.wbNothingYet")}
            </p>
            {canManage && (
              <div className="mt-3 flex justify-center gap-2">
                <AddButtons onAdd={(type) => addNode(type, null)} />
              </div>
            )}
          </Card>
        ) : (
          ordered.map(({ id, node, depth, branch }) => (
            <div key={id} style={{ marginLeft: depth * 24 }}>
              {branch && (
                <p className="mb-1 text-[11px] font-medium text-muted-foreground">
                  {branch === "true" ? t("body.wbIfYes") : t("body.wbIfNo")}
                </p>
              )}
              <NodeCard
                node={node}
                registries={registries}
                selected={selected === id}
                canManage={canManage}
                onSelect={() => setSelected(selected === id ? null : id)}
                onDelete={() => removeNode(id)}
                onAdd={(type) => addNode(type, id)}
              />
              <div className="my-1 flex justify-center text-muted-foreground">
                <ArrowDown className="size-3" />
              </div>
            </div>
          ))
        )}

        {errors.length > 0 && (
          <Card className="border-destructive/40 bg-destructive/5 p-4">
            <p className="flex items-center gap-2 text-sm font-medium text-destructive">
              <AlertTriangle className="size-4" />
              {t("body.wbCannotGoLive")}
            </p>
            <ul className="mt-2 space-y-1 text-xs text-destructive">
              {errors.map((error) => (
                <li key={error}>{error}</li>
              ))}
            </ul>
          </Card>
        )}

        {message && !errors.length && (
          <p className="text-sm text-muted-foreground">{message}</p>
        )}

        {canManage && (
          <div className="flex flex-wrap items-center gap-2 pt-2">
            <Button
              size="sm"
              variant="outline"
              disabled={pending || !dirty}
              onClick={() =>
                run(async () => {
                  await saveDefinition(workflowId, definition);
                  setDirty(false);
                  router.refresh();
                }, t("body.wbDraftSaved"))
              }
            >
              {pending && <Loader2 className="size-4 animate-spin" />}
              {t("body.wbSaveDraft")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={pending}
              onClick={() =>
                run(async () => {
                  const result = await validateDefinition(workflowId, definition);
                  setErrors(result.errors);
                  if (result.valid) setMessage(t("body.wbLooksGood"));
                })
              }
            >
              {t("body.wbCheck")}
            </Button>
            <Button
              size="sm"
              disabled={pending}
              onClick={() =>
                run(async () => {
                  // Saved first: publishing validates the *stored* draft, so
                  // publishing unsaved canvas state would check the wrong thing.
                  await saveDefinition(workflowId, definition);
                  await publishWorkflow(workflowId);
                  setErrors([]);
                  setDirty(false);
                  router.refresh();
                }, t("body.wbPublished"))
              }
            >
              {t("body.wbPublish")}
            </Button>
            {dirty && (
              <span className="text-xs text-muted-foreground">
                {t("body.wbUnsavedChanges")}
              </span>
            )}
          </div>
        )}
      </div>

      {/* ------------------------------------------------------- inspector */}
      <div>
        {selected && nodes[selected] ? (
          <NodeEditor
            key={selected}
            nodeId={selected}
            node={nodes[selected]}
            definition={definition}
            registries={registries}
            triggerEntityType={trigger?.entity_type ?? null}
            readOnly={!canManage}
            onChange={(next) =>
              update({
                ...definition,
                nodes: { ...nodes, [selected]: next },
              })
            }
          />
        ) : (
          <Card className="p-4 text-sm text-muted-foreground">
            {t("body.wbSelectStep")}
          </Card>
        )}
      </div>
    </div>
  );
}

function AddButtons({ onAdd }: { onAdd: (type: WorkflowNodeType) => void }) {
  const { t } = useTranslation();
  return (
    <>
      <Button size="sm" variant="outline" onClick={() => onAdd("action")}>
        <Plus className="size-3.5" />
        {t("body.neAction")}
      </Button>
      <Button size="sm" variant="outline" onClick={() => onAdd("condition")}>
        <GitBranch className="size-3.5" />
        {t("body.neCondition")}
      </Button>
      <Button size="sm" variant="outline" onClick={() => onAdd("delay")}>
        <Clock className="size-3.5" />
        {t("body.neDelay")}
      </Button>
    </>
  );
}

function NodeCard({
  node,
  registries,
  selected,
  canManage,
  onSelect,
  onDelete,
  onAdd,
}: {
  node: WorkflowNode;
  registries: Registries;
  selected: boolean;
  canManage: boolean;
  onSelect: () => void;
  onDelete: () => void;
  onAdd: (type: WorkflowNodeType) => void;
}) {
  const { t } = useTranslation();
  const action = registries.actions.find((entry) => entry.key === node.action);
  const Icon =
    node.type === "condition" ? GitBranch : node.type === "delay" ? Clock : Zap;

  return (
    <>
      <Card
        className={cn(
          "cursor-pointer p-3 transition-colors hover:bg-muted/50",
          selected && "border-primary bg-accent/30",
        )}
        onClick={onSelect}
      >
        <div className="flex items-start gap-3">
          <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
            <Icon className="size-4" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium">
              {node.label || action?.label || t("body.wbUntitledStep")}
            </p>
            <p className="truncate text-xs text-muted-foreground">
              {node.type === "delay"
                ? t("body.wbWaitMinutes", {
                    n: String(node.config?.minutes ?? "?"),
                  })
                : node.type === "condition"
                  ? t("body.wbConditionSummary", {
                      n: node.comparisons?.length ?? 0,
                      mode: node.mode ?? "all",
                    })
                  : (action?.label ?? t("body.wbNoAction"))}
            </p>
          </div>
          {action?.external && (
            // Worth calling out on the canvas: an automation bug that files a
            // task is embarrassing, one that emails clients is not.
            <Badge variant="outline" className="shrink-0">
              {t("body.wbContactsPeople")}
            </Badge>
          )}
          {canManage && (
            <Button
              variant="ghost"
              size="icon-sm"
              className="text-destructive"
              onClick={(event) => {
                event.stopPropagation();
                onDelete();
              }}
              title={t("body.wbRemoveStep")}
            >
              <Trash2 className="size-4" />
            </Button>
          )}
        </div>
      </Card>
      {canManage && (
        <div className="mt-1 flex justify-center gap-1">
          <AddButtons onAdd={onAdd} />
        </div>
      )}
    </>
  );
}

interface ChainEntry {
  id: string;
  node: WorkflowNode;
  depth: number;
  branch: "true" | "false" | null;
}

/**
 * Flatten the tree into render order.
 *
 * Guards against a cycle even though the backend refuses to publish one: a
 * *draft* can contain anything, and a builder that hangs the browser while
 * somebody is mid-edit is worse than one that stops drawing.
 */
function buildChain(definition: WorkflowDefinition): ChainEntry[] {
  const nodes = definition.nodes ?? {};
  const out: ChainEntry[] = [];
  const seen = new Set<string>();

  const walk = (
    id: string | null | undefined,
    depth: number,
    branch: ChainEntry["branch"],
  ) => {
    if (!id || seen.has(id) || !nodes[id]) return;
    seen.add(id);
    const node = nodes[id];
    out.push({ id, node, depth, branch });

    if (node.type === "condition") {
      walk(node.on_true, depth + 1, "true");
      walk(node.on_false, depth + 1, "false");
    } else {
      walk(node.next, depth, null);
    }
  };

  walk(definition.start_node, 0, null);
  return out;
}

function nextNodeId(nodes: Record<string, WorkflowNode>): string {
  let index = 1;
  while (nodes[`n${index}`]) index += 1;
  return `n${index}`;
}
