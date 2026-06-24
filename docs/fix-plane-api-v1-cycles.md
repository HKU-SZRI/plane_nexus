# Plane API v1 Cycles 补全

## 1. Cycles List

**Request**
```
GET /api/v1/workspaces/{slug}/projects/{project_id}/cycles/
```

**Response**
```json
[
  {
    "id": "8df8caf7-6819-4ef7-917f-ff755d6db570",
    "workspace_id": "218cdcae-dd18-46d0-bc31-af8252e54b2b",
    "project_id": "667b1eb1-b8c2-486a-895d-fbf3ce9f2d2c",
    "name": "test2",
    "description": "desc",
    "start_date": "2026-06-23T01:58:20.777291Z",
    "end_date": "2026-06-30T23:59:00Z",
    "owned_by_id": "254e0123-d176-4a43-92da-6137d1a0f090",
    "view_props": {},
    "sort_order": 55535.0,
    "external_source": null,
    "external_id": null,
    "progress_snapshot": {},
    "logo_props": {},
    "version": 1,
    "created_by": "254e0123-d176-4a43-92da-6137d1a0f090",
    "is_favorite": false,
    "total_issues": 4,
    "completed_issues": 1,
    "cancelled_issues": 0,
    "status": "CURRENT",
    "assignee_ids": [
      "31aa809f-9866-414d-bd2d-046f943ca440",
      "4f624f0f-a4fa-4c89-8b42-540b17ef08d8",
      "8fff9558-6f37-4dc6-9020-2a3b9b9b2c8b"
    ]
  }
]
```

---

## 2. Cycle Progress

**Request**
```
GET /api/v1/workspaces/{slug}/projects/{project_id}/cycles/{cycle_id}/progress/
```

**Response**
```json
{
  "total_issues": 7,
  "completed_issues": 7,
  "started_issues": 0,
  "unstarted_issues": 0,
  "cancelled_issues": 0,
  "backlog_issues": 0,
  "total_estimate_points": 0.0,
  "completed_estimate_points": 0,
  "started_estimate_points": 0,
  "unstarted_estimate_points": 0,
  "cancelled_estimate_points": 0,
  "backlog_estimate_points": 0
}
```

---

## 3. Search Issues（添加现有工作项）

**Request**
```
GET /api/v1/workspaces/{slug}/projects/{project_id}/search-issues/?search=&cycle=true&workspace_search=false
```

> `cycle=true` 返回**未加入任何 cycle** 的 issue 列表

**Response**
```json
[
  {
    "id": "6b2ed937-e89a-4e27-a3ec-657698522229",
    "name": "111",
    "sequence_id": 376,
    "project_id": "7019fed3-a98e-4773-b157-9ec0b13ad9b4",
    "project__identifier": "NEXUSMVP",
    "state__name": "Backlog",
    "state__group": "backlog",
    "state__color": "#60646C",
    "start_date": null
  }
]
```

---

## 4. Cycle Date Check

**Request**
```
POST /api/v1/workspaces/{slug}/projects/{project_id}/cycles/date-check/
```
```json
{
  "start_date": "2026-08-01",
  "end_date": "2026-08-31",
  "cycle_id": "a19de6eb-..."
}
```

> `cycle_id` 可选，排除自身（用于编辑时校验）

**Response（无冲突）**
```json
{ "status": true }
```

**Response（有冲突）**
```json
{
  "status": false,
  "error": "You have a cycle already on the given dates, if you want to create a draft cycle you can do that by removing dates"
}
```

---

## 5. User Favorites

**List**
```
GET /api/v1/workspaces/{slug}/user-favorites/
```
```json
[]
```

**Add**
```
POST /api/v1/workspaces/{slug}/user-favorites/
```
```json
{
  "entity_type": "cycle",
  "entity_identifier": "8df8caf7-6819-4ef7-917f-ff755d6db570",
  "entity_data": { "name": "test2" },
  "is_folder": false,
  "parent": null,
  "project_id": "667b1eb1-b8c2-486a-895d-fbf3ce9f2d2c"
}
```

**Remove**
```
DELETE /api/v1/workspaces/{slug}/user-favorites/{favorite_id}/
```
