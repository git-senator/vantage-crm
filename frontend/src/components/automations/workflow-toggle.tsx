"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Switch } from "@/components/ui/switch";
import { setWorkflowEnabled } from "@/lib/api/automations-client";

/**
 * The on/off switch.
 *
 * Separate from publishing on purpose, matching the backend: pausing a
 * misbehaving automation should not require editing or republishing anything.
 * Disabled until a version is published, because there would be nothing to run.
 */
export function WorkflowToggle({
  workflowId,
  isEnabled,
  canManage,
}: {
  workflowId: string;
  isEnabled: boolean;
  canManage: boolean;
}) {
  const router = useRouter();
  const [checked, setChecked] = useState(isEnabled);
  const [pending, setPending] = useState(false);

  return (
    <Switch
      checked={checked}
      disabled={!canManage || pending}
      aria-label={checked ? "Turn workflow off" : "Turn workflow on"}
      onCheckedChange={async (value) => {
        const next = Boolean(value);
        // Optimistic, then reconciled by the refresh. A switch that lags a
        // round trip feels broken, and the failure path puts it back.
        setChecked(next);
        setPending(true);
        try {
          await setWorkflowEnabled(workflowId, next);
          router.refresh();
        } catch {
          setChecked(!next);
        } finally {
          setPending(false);
        }
      }}
    />
  );
}
