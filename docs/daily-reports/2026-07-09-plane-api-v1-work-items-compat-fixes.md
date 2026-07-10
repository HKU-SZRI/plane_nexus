# 2026-07-09 — /api/v1/ work-items 接口修复

修改文件：
- `apps/api/plane/api/views/compat.py`
- `apps/api/plane/api/urls/work_item.py`

背景：`work-items/<pk>/`（PATCH/POST）在早前的 `523186ece3 feat: align issues list and add issue-modules endpoint to /api/v1/` 提交里，从公开 API 自己的 `IssueDetailAPIEndpoint` 改路由到了 `plane.app.IssueViewSet`（Plane 内部 web 前端用的私有 view，经 `IssueV1ViewSet` 包一层 API-Key 认证）。这个 view 的字段名 / 响应格式跟公开 API 文档不一致，导致所有走 `/api/v1/` 的外部客户端（`plane-mcp-server`、NexusUI 代理）在改 work item 时行为不对。以下按接口记录问题现象、根因、修复方式。

---

## 1. PATCH/POST work-items — assignees/labels 字段名 & 响应体

**接口**
```
PATCH /api/v1/workspaces/{slug}/projects/{project_id}/work-items/{pk}/
POST  /api/v1/workspaces/{slug}/projects/{project_id}/work-items/
```

**现象**：外部客户端传 `{"assignees": [...]}` / `{"labels": [...]}`，请求返回 204 却完全没有更新任何数据（字段被静默丢弃）；或者客户端 SDK 直接因为解析空响应体而报错：
```
1 validation error for WorkItem
Input should be a valid dictionary or instance of WorkItem [type=model_type, input_value=None, input_type=NoneType]
```

**根因**：
- `IssueV1ViewSet` 背后的 `IssueCreateSerializer`（`plane.app` 内部序列化器）字段名是 `assignee_ids` / `label_ids`，公开 API 文档承诺的是 `assignees` / `labels`。
- 该内部 view 的 `partial_update()` 固定 `return Response(status=204)`，不返回 body——前端不需要读，但外部 SDK 需要。

**修复**（`IssueV1ViewSet`）：
- 新增 `_normalize_write_fields()`：请求体里 `assignees`/`labels` 存在且 `assignee_ids`/`label_ids` 不存在时，做 key 改名，再交给父类处理，父类逻辑不变。
- `partial_update()` 收到父类返回的 204 时，改为调用 `self.retrieve()` 把更新后的对象查出来，返回 200 + body。

**连带修的一个 bug**：`super().partial_update(request, slug, project_id, pk=pk)` 用位置参数传 `slug`/`project_id`，而 `plane.app.permissions.base.allow_permission` 装饰器读的是 `kwargs["slug"]`——位置参数传不进 `kwargs`，导致 `KeyError: 'slug'` → 500。改成显式关键字传参 `slug=slug, project_id=project_id` 解决。

---

## 2. GET work-items/{pk}/ — assignees 读取镜像

**接口**
```
GET /api/v1/workspaces/{slug}/projects/{project_id}/work-items/{pk}/
```

**现象**：`plane-mcp-server` 里"先读当前 assignee 列表、再增/删一个、再整体写回"的工具（`manage_work_item_assignee` / `manage_work_item_label`）表现成了"整体替换"而不是增量——加一个人会把原有的人顶掉。

**根因**：响应里只有扁平的 `assignee_ids: [uuid, ...]`，SDK 的 `WorkItemDetail.assignees` 字段要的是对象数组 `[{"id": ...}, ...]`。字段缺失时 pydantic 用默认值空列表兜底，"读取当前列表"永远读到 `[]`，"追加一个"就变成了"只剩这一个"。

**修复**（`IssueV1ViewSet.retrieve()`）：响应里补一份 `assignees` 镜像，从已有的 `assignee_ids` 派生：
```python
response.data.setdefault("assignees", [{"id": uid} for uid in assignee_ids])
```
`labels` 同理受影响（`Label` 模型 `name` 是必填字段，无法只用 id 拼最小对象），**尚未修复**，目前没有实际报障用例，先记录。

---

## 3. GET work-items/search/ — 查询参数名

**接口**
```
GET /api/v1/workspaces/{slug}/work-items/search/
GET /api/v1/workspaces/{slug}/issues/search/   （deprecated 别名，一并修）
```

**现象**：`search_work_items` 工具永远返回 `{"issues": []}`，不管搜什么都搜不到。

**根因**：这个端点本身没被动过（就是原版公开 API `plane.api.views.issue.IssueSearchEndpoint`），问题在第三方 `plane-mcp-server` SDK 自己：它的 OpenAPI 文档写的查询参数名是 `search`，但 SDK 发请求时用的 key 是 `q`（`plane/api/work_items/base.py`: `search_params = {"q": query}`）——纯粹是上游 SDK 自己的 bug，不是这边路由改出来的。

**修复**：新增 `WorkItemSearchV1Endpoint`（继承原版 `IssueSearchEndpoint`，不改内部逻辑），请求里有 `q` 没有 `search` 时把 `q` 镜像成 `search` 再转发；`work_item.py` 里两条 search 路由都切到这个类。

**又发现一个连带 bug**：参数名修好后，返回的 `sequence_id` 是 Django `IntegerField`（`issues.values("sequence_id")` 直接输出数字），但 SDK 的 `WorkItemSearchItem.sequence_id` 声明成了 `str`，解析仍然报错：
```
1 validation error for WorkItemSearch
issues.0.sequence_id
  Input should be a valid string [type=string_type, input_value=6, input_type=int]
```
同样是 SDK 自己模型类型声明和端点实际返回类型对不上，不是路由问题。修法：`WorkItemSearchV1Endpoint.get()` 里对 `response.data["issues"]` 每条的 `sequence_id` 做 `str()` 转换再返回。

**第三个问题（功能缺口，不是 bug）**：`search_work_items` 工具支持传 `fields`/`expand` 参数（比如 `expand=assignees,labels,state,project`，`fields=priority,created_at`），但原版 `IssueSearchEndpoint.get()` 从来没读过这两个参数——`request.query_params` 只取了 `search`/`limit`/`workspace_search`/`project_id`，`.values()` 永远只查那 6 个固定字段，多传的参数全部静默忽略。

**修复**：`WorkItemSearchV1Endpoint.get()` 不再调用 `super().get()`，改为复刻原查询逻辑（同样的 `Issue.issue_objects.filter(...)` + 项目成员校验）并加上：
- `fields=`：白名单里的 Issue 普通列（`priority`/`type_id`/`description_html`/`created_at`/`updated_at`/`start_date`/`target_date`/`sort_order`）按需加进 `.values()`。
- `expand=state`：额外查 `state_id`/`state__name`/`state__group`，组装成嵌套 `{"id","name","group"}`。
- `expand=project`：额外查 `project__name`，组装成嵌套 `{"id","identifier","name"}`。
- `expand=assignees`/`expand=labels`：用 `ArrayAgg` annotate 出 `assignee_ids`/`label_ids`，转成 `[{"id": ...}, ...]`。

`WorkItemSearchItem` 这个 SDK 模型是 `extra="allow"`，多出来的字段不需要在 SDK 那边声明对应类型也能正常透传给 LLM，所以这次不用等第三方模型更新。不传 `fields`/`expand` 时行为跟以前完全一致（只返回原来那 6 个字段），向后兼容。

---

## 4. 全量场景扫描（用 plane SDK 直测所有常用工具链路）

按 Plane Agent 常用场景（建任务、查人、搜索、分配、周报、迭代、模块、评论、标签）把 `plane-mcp-server` 依赖的 `plane` SDK 直接指向本地 API 逐一实测。

**通过（无需改动）**：`list_projects`、`list_states`、`list_work_items`（含 `pql`/`order_by`/`per_page`）、`retrieve_work_item`、`list_labels`、`get_workspace_members`、`list_work_item_comments`、`list_work_item_activities`、`list_work_item_links`、`get_me`、`list_intake_work_items`。

**发现并修复**：

- **`list_cycles` / `list_modules` / `list_project_pages` 全挂**：compat 内部视图返回扁平 JSON 数组，SDK 期望分页信封 `{"results": [...], "count", "next_cursor", ...}`（`PaginatedResponse` 所有分页字段均必填）。修法：`compat.py` 新增 `_PaginatedListShimMixin`，`list()` 返回数组时包成单页信封，套到 `CycleV1ViewSet`/`ModuleV1ViewSet`/`PageV1ViewSet`。nexus-ui 侧安全——它统一走 `unwrapPaginatedResults()`（`nexus-ui/src/features/projects/model/utils.ts:58`），数组和信封两种形状都兼容，无需改前端。
- **labels 读取镜像**（之前记录"尚未修复"的那条）：`manage_work_item_label` 的读-改-写路径读不到 `labels` 对象数组。SDK 的 `Label` 模型 `name` 必填，不能只镜像 id——`IssueV1ViewSet.retrieve()` 里用一次 `Label.objects.filter(id__in=...).values("id","name")` 查询补全。已用"建临时 label → 加到 work item → 读回名字 → 还原 → 删 label"完整闭环验证。

**404 缺口（本 fork 未实现的路由，未修，先记录）**：
- `count_work_items`（`GET /workspaces/{slug}/work-items/count`）——无此路由，周报统计场景 agent 会退化用 `list_work_items` 自己数。
- `list_work_item_types` / `resolve_work_item_type` 等 work-item-types 工具——无路由（疑似 EE 功能）。
- `attach_page_to_work_item` / `list_work_item_pages`（`/work-items/{id}/pages/`）——无路由。

---

## 影响范围

- 只影响 `/api/v1/` 这一条 URL 命名空间（API-Key 认证的外部客户端：`plane-mcp-server`、NexusUI 经 `nexus/plane_proxy.py` 代理的请求）。
- Plane 网页前端走的是 `plane.app` 自己的 URL（session 认证，未经过 `compat.py`），未受影响。
- NexusUI 现有请求已经在用内部字段名 `assignee_ids`（`nexus-ui/src/api/projects.ts:667-671`），本次改动对它是透传，不受影响；它的 `labels` 字段（发的是公开名 `labels`）此前被静默丢弃，现在顺带修复。

## 部署状态

代码已改完并在本地 `docker compose build api && docker compose up -d api` 验证通过，**尚未提交 / 未推送 CI**。
