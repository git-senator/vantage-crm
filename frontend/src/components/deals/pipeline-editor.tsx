"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { ChevronDown, ChevronUp, Loader2, Plus, Trash2, TriangleAlert } from "lucide-react";

import { StatusBadge } from "@/components/shared/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { ClientApiError } from "@/lib/api/client";
import {
  addPipelineStage,
  deletePipelineStage,
  updatePipelineStage,
} from "@/lib/api/deals-client";
import type { Pipeline } from "@/lib/api/types";

/** "Under contract" -> "under_contract". Keys are machine names. */
function toKey(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 40);
}

/**
 * Stage configuration.
 *
 * Reordering swaps `position` between two stages rather than rewriting the
 * whole list — the column is deliberately not unique server-side precisely so
 * a swap is two independent updates and not a three-step dance.
 *
 * Deleting a stage that still holds deals returns 409 with the count. That
 * message is surfaced verbatim, because "3 deals are still here" is the only
 * useful thing to say at that moment.
 */
export function PipelineEditor({ pipeline }: { pipeline: Pipeline }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [newName, setNewName] = useState("");

  const stages = [...pipeline.stages].sort((a, b) => a.position - b.position);

  async function run(key: string, action: () => Promise<unknown>) {
    setBusy(key);
    setError(null);
    try {
      await action();
      router.refresh();
    } catch (caught) {
      setError(
        caught instanceof ClientApiError
          ? caught.message
          : "Something went wrong. Please try again.",
      );
    } finally {
      setBusy(null);
    }
  }

  async function swap(indexA: number, indexB: number) {
    const a = stages[indexA];
    const b = stages[indexB];
    if (!a || !b) return;
    await run(`move-${a.id}`, async () => {
      await updatePipelineStage(pipeline.id, a.id, { position: b.position });
      await updatePipelineStage(pipeline.id, b.id, { position: a.position });
    });
  }

  return (
    <div className="max-w-3xl space-y-6">
      {error && (
        <div
          role="alert"
          className="flex items-start gap-2.5 rounded-lg border border-destructive/30 bg-destructive/8 p-3 text-sm text-destructive"
        >
          <TriangleAlert className="mt-0.5 size-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Stages</CardTitle>
        </CardHeader>
        <CardContent className="space-y-0">
          {stages.map((stage, index) => (
            <div key={stage.id}>
              <div className="flex items-center gap-3 py-3">
                <div className="flex flex-col">
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`Move ${stage.name} earlier`}
                    disabled={index === 0 || busy !== null}
                    onClick={() => swap(index, index - 1)}
                  >
                    <ChevronUp className="size-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`Move ${stage.name} later`}
                    disabled={index === stages.length - 1 || busy !== null}
                    onClick={() => swap(index, index + 1)}
                  >
                    <ChevronDown className="size-4" />
                  </Button>
                </div>

                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{stage.name}</p>
                  <p className="truncate font-mono text-xs text-muted-foreground">
                    {stage.key}
                  </p>
                </div>

                <span className="tabular text-xs text-muted-foreground">
                  {stage.default_probability}%
                </span>

                {stage.is_won && <StatusBadge status="won" />}
                {stage.is_lost && <StatusBadge status="lost" />}

                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`Remove ${stage.name}`}
                  className="text-destructive"
                  disabled={busy !== null || stages.length <= 1}
                  onClick={() =>
                    run(`delete-${stage.id}`, () =>
                      deletePipelineStage(pipeline.id, stage.id),
                    )
                  }
                >
                  {busy === `delete-${stage.id}` ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Trash2 className="size-4" />
                  )}
                </Button>
              </div>
              <Separator className="last:hidden" />
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Add a stage</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-end gap-2">
            <div className="flex-1 space-y-2">
              <Label htmlFor="new_stage">Name</Label>
              <Input
                id="new_stage"
                placeholder="Inspection"
                value={newName}
                onChange={(event) => setNewName(event.target.value)}
                disabled={busy !== null}
              />
              {newName.trim() !== "" && (
                <p className="font-mono text-xs text-muted-foreground">
                  key: {toKey(newName)}
                </p>
              )}
            </div>
            <Button
              disabled={busy !== null || toKey(newName) === ""}
              onClick={() =>
                run("add", async () => {
                  await addPipelineStage(pipeline.id, {
                    key: toKey(newName),
                    name: newName.trim(),
                    // Appended at the end; reorder afterwards.
                    position: stages.length,
                  });
                  setNewName("");
                })
              }
            >
              {busy === "add" ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Plus className="size-4" />
              )}
              Add stage
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
