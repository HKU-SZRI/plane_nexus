/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { useState } from "react";
import { observer } from "mobx-react";
// plane imports
import { Button } from "@plane/propel/button";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import type { TIssue } from "@plane/types";
import { EModalPosition, EModalWidth, ModalCore } from "@plane/ui";
import { generateWorkItemLink } from "@plane/utils";
// hooks
import { useProject } from "@/hooks/store/use-project";
import { useAppRouter } from "@/hooks/use-app-router";
// services
import { IssueService } from "@/services/issue/issue.service";
// local imports
import { ProjectDropdown } from "../dropdowns/project/dropdown";

const issueService = new IssueService();

type Props = {
  workspaceSlug: string;
  issue: TIssue;
  isOpen: boolean;
  handleClose: () => void;
};

/**
 * ponytail: quick internal tool to exercise the /transfer/ endpoint from the UI
 * for manual testing — not wired through the MobX issue stores, so the issue
 * list/board on screen won't update itself; reload to see the original marked
 * Completed.
 */
export const TransferIssueModal = observer(function TransferIssueModal(props: Props) {
  const { workspaceSlug, issue, isOpen, handleClose } = props;
  const [targetProjectId, setTargetProjectId] = useState<string | null>(null);
  const [isTransferring, setIsTransferring] = useState(false);
  const { getProjectIdentifierById } = useProject();
  const router = useAppRouter();

  const onClose = () => {
    setTargetProjectId(null);
    setIsTransferring(false);
    handleClose();
  };

  const handleTransfer = async () => {
    if (!targetProjectId || !issue.project_id) return;
    setIsTransferring(true);
    try {
      const result = await issueService.transferIssue(workspaceSlug, issue.project_id, issue.id, targetProjectId);
      setToast({
        type: TOAST_TYPE.SUCCESS,
        title: "Work item transferred",
        message: `Moved to ${getProjectIdentifierById(targetProjectId)}-${result.sequence_id}`,
      });
      onClose();
      router.push(
        generateWorkItemLink({
          workspaceSlug,
          projectId: result.project_id,
          issueId: result.id,
          projectIdentifier: getProjectIdentifierById(result.project_id),
          sequenceId: result.sequence_id,
        })
      );
    } catch (error: any) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: "Transfer failed",
        message: error?.error ?? "Something went wrong, please try again.",
      });
    } finally {
      setIsTransferring(false);
    }
  };

  return (
    <ModalCore isOpen={isOpen} handleClose={onClose} position={EModalPosition.CENTER} width={EModalWidth.LG}>
      <div className="px-5 py-4">
        <h3 className="text-18 font-medium 2xl:text-20">Move to project</h3>
        <p className="mt-3 text-13 text-secondary">
          Creates a copy of this work item in the target project (carrying over assignees, attachments, comments and
          activity history) and marks this one Completed.
        </p>
        <div className="mt-4">
          <ProjectDropdown
            value={targetProjectId}
            onChange={(val) => setTargetProjectId(val)}
            multiple={false}
            buttonVariant="border-with-text"
            renderCondition={(projectId) => projectId !== issue.project_id}
            placeholder="Select target project"
          />
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" size="lg" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            size="lg"
            onClick={handleTransfer}
            loading={isTransferring}
            disabled={!targetProjectId}
          >
            {isTransferring ? "Moving..." : "Move"}
          </Button>
        </div>
      </div>
    </ModalCore>
  );
});
