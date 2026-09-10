/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { ArrowRightLeft } from "lucide-react";
// hooks
import { useIssueDetail } from "@/hooks/store/use-issue-detail";
// local imports
import { IssueActivityBlockComponent } from "./";

type TIssueTransferActivity = { activityId: string; ends: "top" | "bottom" | undefined };

export const IssueTransferActivity = observer(function IssueTransferActivity(props: TIssueTransferActivity) {
  const { activityId, ends } = props;
  const {
    activity: { getActivityById },
  } = useIssueDetail();

  const activity = getActivityById(activityId);
  if (!activity) return <></>;

  const movedOut = !!activity.new_identifier;

  return (
    <IssueActivityBlockComponent
      activityId={activityId}
      icon={<ArrowRightLeft className="h-3.5 w-3.5 text-secondary" aria-hidden="true" />}
      ends={ends}
    >
      {movedOut ? (
        <>
          moved this work item to <span className="font-medium">{activity.new_value}</span>
        </>
      ) : (
        <>
          moved this work item from <span className="font-medium">{activity.old_value}</span>
        </>
      )}
      .
    </IssueActivityBlockComponent>
  );
});
