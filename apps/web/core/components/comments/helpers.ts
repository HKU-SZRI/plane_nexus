/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import type { EditorRefApi } from "@plane/editor";
import { getEditorAssetDownloadSrc } from "@plane/utils";

export const getCommentAttachmentBlockId = () =>
  `comment-attachment-${Date.now()}-${Math.random().toString(36).slice(2)}`;

const escapeHtml = (value: string) =>
  value.replace(
    /[&<>"']/g,
    (char) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[char] ?? char
  );

export const insertCommentAttachmentLink = ({
  assetId,
  editorRef,
  fileName,
  projectId,
  workspaceSlug,
}: {
  assetId: string;
  editorRef: EditorRefApi;
  fileName: string;
  projectId?: string;
  workspaceSlug: string;
}) => {
  const href = getEditorAssetDownloadSrc({ assetId, projectId, workspaceSlug });
  if (!href) return;

  const label = fileName.trim() || "attachment";
  editorRef.setEditorValueAtCursorPosition(
    `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`
  );
};
