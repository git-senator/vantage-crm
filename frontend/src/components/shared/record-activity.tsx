import { Clock, FileText, StickyNote } from "lucide-react";

import { AttachmentsPanel } from "@/components/attachments/attachments-panel";
import { NotesPanel } from "@/components/notes/notes-panel";
import { Timeline } from "@/components/timeline/timeline";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { listAttachmentsForEntity } from "@/lib/api/attachments";
import { listNotesForEntity } from "@/lib/api/notes";
import { getEntityTimeline } from "@/lib/api/timeline";
import type { RecordEntityType } from "@/lib/api/types";

/**
 * The shared "what has happened here" block, dropped into every record's detail
 * page: the merged timeline, editable notes, and attached files. A server
 * component — it fetches all three sources in parallel, then hands the notes and
 * attachments to their client panels for interaction. One import per detail
 * page, so leads/clients/properties/deals stay consistent by construction.
 */
export async function RecordActivity({
  entityType,
  entityId,
  canManageNotes,
  canManageDocuments,
}: {
  entityType: RecordEntityType;
  entityId: string;
  canManageNotes: boolean;
  canManageDocuments: boolean;
}) {
  const [timeline, notes, attachments] = await Promise.all([
    getEntityTimeline(entityType, entityId),
    listNotesForEntity(entityType, entityId),
    listAttachmentsForEntity(entityType, entityId),
  ]);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <StickyNote className="size-4" />
            Notes
          </CardTitle>
        </CardHeader>
        <CardContent>
          <NotesPanel
            entityType={entityType}
            entityId={entityId}
            notes={notes}
            canManage={canManageNotes}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Clock className="size-4" />
            Timeline
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Timeline items={timeline} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <FileText className="size-4" />
            Files
          </CardTitle>
        </CardHeader>
        <CardContent>
          <AttachmentsPanel
            entityType={entityType}
            entityId={entityId}
            attachments={attachments}
            canManage={canManageDocuments}
          />
        </CardContent>
      </Card>
    </div>
  );
}
