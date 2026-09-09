/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { useParams } from "next/navigation";
import { ArchiveX } from "lucide-react";
// plane imports
import { EUserPermissions, EUserPermissionsLevel } from "@plane/constants";
import { useTranslation } from "@plane/i18n";
import type { IProject } from "@plane/types";
import { ToggleSwitch } from "@plane/ui";
import { SettingsControlItem } from "@/components/settings/control-item";
// hooks
import { useProject } from "@/hooks/store/use-project";
import { useUserPermissions } from "@/hooks/store/user";

type Props = {
  handleChange: (formData: Partial<IProject>) => Promise<void>;
};

export const AutoArchiveCancelledAutomation = observer(function AutoArchiveCancelledAutomation(props: Props) {
  const { handleChange } = props;
  const { workspaceSlug } = useParams();
  const { currentProjectDetails } = useProject();
  const { allowPermissions } = useUserPermissions();
  const { t } = useTranslation();

  const isAdmin = allowPermissions(
    [EUserPermissions.ADMIN],
    EUserPermissionsLevel.PROJECT,
    workspaceSlug?.toString(),
    currentProjectDetails?.id
  );

  const isEnabled = currentProjectDetails?.auto_archive_cancelled_issues ?? true;

  return (
    <div className="flex items-center gap-3 border-b border-subtle py-2">
      <div className="grid size-10 shrink-0 place-items-center rounded-sm bg-layer-2">
        <ArchiveX className="size-4 shrink-0 text-danger-primary" />
      </div>
      <SettingsControlItem
        title={t("project_settings.automations.auto-archive-cancelled.title")}
        description={t("project_settings.automations.auto-archive-cancelled.description")}
        control={
          <ToggleSwitch
            value={isEnabled}
            onChange={() => void handleChange({ auto_archive_cancelled_issues: !isEnabled })}
            size="sm"
            disabled={!isAdmin}
          />
        }
      />
    </div>
  );
});
