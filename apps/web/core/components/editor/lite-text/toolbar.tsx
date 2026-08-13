/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import { Paperclip } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { EIssueCommentAccessSpecifier } from "@plane/constants";
// editor
import type { EditorRefApi } from "@plane/editor";
// i18n
import { useTranslation } from "@plane/i18n";
// ui
import { Button } from "@plane/propel/button";
import { GlobeIcon, LockIcon } from "@plane/propel/icons";
import type { ISvgIcons } from "@plane/propel/icons";
import { TOAST_TYPE, setToast } from "@plane/propel/toast";
import { Tooltip } from "@plane/propel/tooltip";
// constants
import { cn } from "@plane/utils";
import type { ToolbarMenuItem } from "@/constants/editor";
import { TOOLBAR_ITEMS } from "@/constants/editor";
// helpers

type Props = {
  accessSpecifier?: EIssueCommentAccessSpecifier;
  executeCommand: (item: ToolbarMenuItem) => void;
  handleAccessChange?: (accessKey: EIssueCommentAccessSpecifier) => void;
  handleAttachmentUpload?: (file: File, onUploadProgress?: (progress: number) => void) => Promise<void>;
  handleSubmit: (event: React.MouseEvent<HTMLButtonElement, MouseEvent>) => void;
  isCommentEmpty: boolean;
  isSubmitting: boolean;
  maxFileSize: number;
  showAccessSpecifier: boolean;
  showSubmitButton: boolean;
  editorRef: EditorRefApi | null;
  submitButtonText?: string;
};

type TCommentAccessType = {
  icon: LucideIcon | React.FC<ISvgIcons>;
  key: EIssueCommentAccessSpecifier;
  label: "Private" | "Public";
};

const COMMENT_ACCESS_SPECIFIERS: TCommentAccessType[] = [
  {
    icon: LockIcon,
    key: EIssueCommentAccessSpecifier.INTERNAL,
    label: "Private",
  },
  {
    icon: GlobeIcon,
    key: EIssueCommentAccessSpecifier.EXTERNAL,
    label: "Public",
  },
];

const toolbarItems = TOOLBAR_ITEMS.lite;

export function IssueCommentToolbar(props: Props) {
  const { t } = useTranslation();
  const {
    accessSpecifier,
    executeCommand,
    handleAccessChange,
    handleAttachmentUpload,
    handleSubmit,
    isCommentEmpty,
    isSubmitting,
    maxFileSize,
    showAccessSpecifier,
    showSubmitButton,
    editorRef,
    submitButtonText = "common.comment",
  } = props;
  // State to manage active states of toolbar items
  const [activeStates, setActiveStates] = useState<Record<string, boolean>>({});
  const [isAttachmentUploading, setIsAttachmentUploading] = useState(false);
  const [attachmentUploadProgress, setAttachmentUploadProgress] = useState(0);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Function to update active states
  const updateActiveStates = useCallback(() => {
    if (!editorRef) return;
    const newActiveStates: Record<string, boolean> = {};
    Object.values(toolbarItems)
      .flat()
      .forEach((item) => {
        // TODO: update this while toolbar homogenization
        // @ts-expect-error type mismatch here
        newActiveStates[item.renderKey] = editorRef.isMenuItemActive({
          itemKey: item.itemKey,
          ...item.extraProps,
        });
      });
    setActiveStates(newActiveStates);
  }, [editorRef]);

  // useEffect to call updateActiveStates when isActive prop changes
  useEffect(() => {
    if (!editorRef) return;
    const unsubscribe = editorRef.onStateChange(updateActiveStates);
    updateActiveStates();
    return () => unsubscribe();
  }, [editorRef, updateActiveStates]);

  const isEditorReadyToDiscard = editorRef?.isEditorReadyToDiscard();
  const isSubmitButtonDisabled = isCommentEmpty || !isEditorReadyToDiscard || isAttachmentUploading;

  const handleFileChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !handleAttachmentUpload) return;

    if (file.size > maxFileSize) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: "File too large",
        message: `File must be ${maxFileSize / 1024 / 1024}MB or less in size.`,
      });
      return;
    }

    try {
      setIsAttachmentUploading(true);
      setAttachmentUploadProgress(0);
      await handleAttachmentUpload(file, setAttachmentUploadProgress);
      setAttachmentUploadProgress(100);
    } catch (error) {
      setToast({
        type: TOAST_TYPE.ERROR,
        title: "Upload failed",
        message: error instanceof Error ? error.message : "Attachment upload failed.",
      });
    } finally {
      setIsAttachmentUploading(false);
    }
  };

  return (
    <div className="flex w-full flex-col gap-1">
      <div className="flex h-9 w-full items-stretch gap-1.5 overflow-x-scroll bg-surface-2">
        {showAccessSpecifier && (
          <div className="flex flex-shrink-0 items-stretch gap-0.5 rounded-sm border-[0.5px] border-subtle p-1">
            {COMMENT_ACCESS_SPECIFIERS.map((access) => {
              const isAccessActive = accessSpecifier === access.key;

              return (
                <Tooltip key={access.key} tooltipContent={access.label}>
                  <button
                    type="button"
                    onClick={() => handleAccessChange?.(access.key)}
                    className={cn("grid aspect-square place-items-center rounded-xs p-1 hover:bg-layer-1", {
                      "bg-layer-1": isAccessActive,
                    })}
                  >
                    <access.icon
                      className={cn("h-3.5 w-3.5 text-placeholder", {
                        "text-primary": isAccessActive,
                      })}
                      strokeWidth={2}
                    />
                  </button>
                </Tooltip>
              );
            })}
          </div>
        )}
        <div className="flex w-full items-stretch justify-between gap-2 rounded-sm border-[0.5px] border-subtle p-1">
          <div className="flex items-stretch">
            {Object.keys(toolbarItems).map((key, index) => (
              <div
                key={key}
                className={cn("flex items-stretch gap-0.5 border-r border-subtle px-2.5", {
                  "pl-0": index === 0,
                })}
              >
                {toolbarItems[key].map((item) => {
                  const isItemActive = activeStates[item.renderKey];

                  return (
                    <Tooltip
                      key={item.renderKey}
                      tooltipContent={
                        <p className="flex flex-col gap-1 text-center text-11">
                          <span className="font-medium">{item.name}</span>
                          {item.shortcut && <kbd className="text-placeholder">{item.shortcut.join(" + ")}</kbd>}
                        </p>
                      }
                    >
                      <button
                        type="button"
                        onClick={() => executeCommand(item)}
                        className={cn(
                          "grid aspect-square place-items-center rounded-xs p-0.5 text-placeholder hover:bg-layer-1",
                          {
                            "bg-layer-1 text-primary": isItemActive,
                          }
                        )}
                      >
                        <item.icon
                          className={cn("h-3.5 w-3.5", {
                            "text-primary": isItemActive,
                          })}
                          strokeWidth={2.5}
                        />
                      </button>
                    </Tooltip>
                  );
                })}
              </div>
            ))}
            {handleAttachmentUpload && (
              <div className="flex items-stretch gap-0.5 px-2.5">
                <input ref={fileInputRef} type="file" className="hidden" onChange={handleFileChange} />
                <Tooltip tooltipContent={isAttachmentUploading ? "Uploading attachment" : "Attachment"}>
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={isAttachmentUploading}
                    className="grid aspect-square place-items-center rounded-xs p-0.5 text-placeholder hover:bg-layer-1 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <Paperclip className="h-3.5 w-3.5" strokeWidth={2.5} />
                  </button>
                </Tooltip>
              </div>
            )}
          </div>
          {showSubmitButton && (
            <div className="sticky right-1">
              <Button
                type="submit"
                variant="primary"
                className="px-2.5 py-1.5 text-11"
                onClick={handleSubmit}
                disabled={isSubmitButtonDisabled}
                loading={isSubmitting}
              >
                {t(submitButtonText)}
              </Button>
            </div>
          )}
        </div>
      </div>
      {isAttachmentUploading && (
        <div className="h-1 w-full overflow-hidden rounded-full bg-layer-2" aria-label="Attachment upload progress">
          <div
            className="h-full rounded-full bg-primary transition-[width] duration-150 ease-out"
            style={{ width: `${attachmentUploadProgress}%` }}
          />
        </div>
      )}
    </div>
  );
}
