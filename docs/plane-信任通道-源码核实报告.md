# Plane 可信服务通道源码核实报告

## 1. 核实范围

本报告核对 `docs/plane-信任通道-实施计划.md` 中与 Plane fork 相关的源码假设，重点检查：

- `/api/` 与 `/api/v1/` 的路由归属；
- API 认证类的实际覆盖范围；
- `plane.app.permissions` 与 `plane.utils.permissions` 的重复情况；
- `permission_classes` 和 `allow_permission` 两层权限判断；
- 活动记录与 webhook 的 actor 归因；
- Docker Compose 和 Caddy 的网络入口；
- 现有测试及后续测试应覆盖的边界。

前 10 节是实施前的静态核实记录；第 11 节记录当前分支 `feat/plane-trusted-service-channel` 的实际实现与运行验证。

## 2. 总体结论

原计划的总体方向成立：Nexus 应使用短时签名凭据调用 `/api/v1/`，Plane 将令牌中的真实用户解析为 `request.user`，并为已经经过 Nexus 判权的全部请求跳过 Plane 原生角色判断。

但原计划目前不能直接实施，主要有以下问题：

1. 认证入口不只有 `BaseAPIView`，还包括 `BaseViewSet` 和 `compat.py` 中 36 处显式认证覆盖。
2. `compat.py` 继承了 `plane.app` 视图，因此继承方法上的 `allow_permission` 仍会阻止可信写请求。
3. `method + path + exp` 没有绑定请求 body，也不能防止同一 JWT 被重放。
4. Nexus 选择 Compose 内网地址发请求，并不能阻止公网请求携带可信 Header 到达同一个 API 服务。
5. 部分现有 `/api/v1/` 写接口允许请求体中的 `created_by` 覆盖活动 actor，不能只依赖 `request.user` 自动保证审计真实性。
6. 原计划列出的 permission import 文件少了 `estimate.py`，并且没有覆盖 compat 视图的间接权限依赖。

应先修订实现范围和测试方案，再开始编码。

## 3. 已确认正确的结论

### 3.1 URL 命名空间确实分离

`apps/api/plane/urls.py` 当前注册关系是：

```python
path("api/", include("plane.app.urls"))
path("api/public/", include("plane.space.urls"))
path("api/instances/", include("plane.license.urls"))
path("api/v1/", include("plane.api.urls"))
```

因此外部 API-Key API 的入口确实是 `plane.api`，URL 前缀是 `/api/v1/`。Space 和 License 使用独立视图基类及认证配置，不应直接加入本次可信通道。

需要注意的是，URL 属于 `plane.api` 不代表运行时只执行 `plane/api/views` 中的代码；`plane.api.views.compat` 会继承并执行大量 `plane.app.views` 的实现。

### 3.2 三组 permission 文件确实重复

以下文件当前逐字节相同：

- `plane/app/permissions/project.py` 与 `plane/utils/permissions/project.py`
- `plane/app/permissions/workspace.py` 与 `plane/utils/permissions/workspace.py`
- `plane/app/permissions/page.py` 与 `plane/utils/permissions/page.py`
- 两边的 `permissions/__init__.py`

两边的 `base.py` 不相同，不能一起机械合并。其中 `plane.app.permissions.base.allow_permission` 比 utils 版本多一段 workspace membership 检查。

仓库内没有发现依赖这两套 permission 类对象身份不同的 `isinstance`、`issubclass` 或 `is` 判断。把 utils 下的 `project.py`、`workspace.py`、`page.py` 改成从 app 权限模块重新导出，在当前仓库内是合理方向，但仍应运行全量回归测试，以覆盖仓库外部调用者和动态导入。

### 3.3 多数活动记录使用 `request.user`

以 compat work-item 写路径为例，创建、更新和删除操作均把 `request.user.id` 传入 `issue_activity`、`model_activity` 或 `webhook_activity`。`webhook_activity` 随后根据 actor ID 序列化真实用户。

Plane 的 `BaseModel.save()` 也通过 CRUM 的当前请求用户自动设置 `created_by` 或 `updated_by`。因此，如果内部认证正确设置 DRF 的 `request.user`，多数现有写路径可以保留真实用户归因。

这仍需要端到端测试，不能只从调用参数推断所有接口都正确，见第 7 节。

### 3.4 当前容器的主要公网入口是 proxy

根目录 `docker-compose.yml` 中 `proxy` 映射宿主机 `80:80`，`api` 服务没有直接映射宿主机端口。Caddy 将 `/api/*` 反向代理到 `api:8000`。

生产 API 镜像通过 `apps/api/Dockerfile.api` 构建，兼容改动上线前确实需要重新 build 并启动 API 服务。

## 4. 需要修正的源码假设

### 4.1 只修改 `BaseAPIView` 不能覆盖 `/api/v1/`

`plane/api/views/base.py` 同时定义了两个公共基类：

```python
class BaseAPIView(...):
    authentication_classes = [APIKeyAuthentication]

class BaseViewSet(...):
    authentication_classes = [APIKeyAuthentication]
```

`WorkspaceInvitationsViewset` 和 `StickyViewSet` 等接口继承的是 `BaseViewSet`。因此，即使不考虑 compat 视图，也至少需要同时修改这两个基类。

更大的遗漏在 `plane/api/views/compat.py`：其中有 36 个兼容视图显式声明：

```python
authentication_classes = [APIKeyAuthentication]
```

这些类继承 `plane.app` 视图，以便在 `/api/v1/` 下复用内部 Web API 行为。work-item、cycle、module、page、state、关系、订阅和附件等 Nexus 常用路由都包含在内。父类认证配置不会覆盖这些显式声明。

建议定义一份共享认证配置，例如：

```python
API_AUTHENTICATION_CLASSES = [APIKeyAuthentication, InternalServiceAuthentication]
```

由 `BaseAPIView`、`BaseViewSet` 和全部 compat 包装类共同引用，避免以后只更新其中一处。

还应明确混合凭据行为。DRF 会按顺序调用认证器：

- 请求不带 `X-Api-Key` 时，现有 API-Key 认证返回 `None`，可以继续尝试内部认证；
- 请求带有无效 `X-Api-Key` 时，现有认证器会抛出 `AuthenticationFailed`，不会继续尝试下一个认证器；
- 两种凭据同时存在且 API Key 有效时，前面的 API-Key 认证会胜出，请求不会被标记为可信调用。

推荐显式拒绝同时携带两种凭据的请求，避免调用方误以为内部授权已经生效。

### 4.2 `allow_permission` 仍位于 `/api/v1/` 写路径上

原计划通过搜索 `plane/api/views/*.py` 中的装饰器用法，得出 `allow_permission` 不影响本次改动。这个检查忽略了继承关系。

例如：

```python
class IssueV1ViewSet(_IssueViewSet):
    authentication_classes = [APIKeyAuthentication]
```

`_IssueViewSet` 来自 `plane.app.views.issue.base.IssueViewSet`。其方法已经被装饰：

```python
@allow_permission([ROLE.ADMIN, ROLE.MEMBER])
def create(...): ...

@allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER], creator=True, model=Issue)
def partial_update(...): ...

@allow_permission([ROLE.ADMIN], creator=True, model=Issue)
def destroy(...): ...
```

因此，即使 `permission_classes` 已经旁路，可信 Guest 写请求仍会在继承下来的装饰器中返回 403。cycle、module、state 等 compat 视图也存在相同情况。

`plane.app.views` 中共有 38 个文件、195 处 `@allow_permission`。本次不需要无条件放开这些 Web API，只需要让装饰器识别由内部认证器设置的可信标记，并且仅对非安全方法旁路：

```python
if (
    getattr(request, "is_trusted_nexus_call", False)
    and request.method not in SAFE_METHODS
):
    return view_func(instance, request, *args, **kwargs)
```

可信标记只能由成功完成全部令牌约束校验的认证器设置，不能直接相信客户端提交的普通 Header。

### 4.3 permission import 清单不完整

当前 `plane/api/views` 中直接从 `plane.app.permissions` 导入权限类的文件有 9 个：

- `cycle.py`
- `estimate.py`
- `intake.py`
- `issue.py`
- `issue_type.py`
- `module.py`
- `project.py`
- `state.py`
- `sticky.py`

原计划的 8 个文件漏了 `estimate.py`。另外两个从 `plane.utils.permissions` 导入的文件是：

- `invite.py`
- `member.py`

这 9+2 个文件仍不是完整的运行时权限清单，因为 `compat.py` 会间接继承 `plane.app` 的 permission classes 和方法装饰器。后续测试应从 Django URL resolver 的全部 `/api/v1/` 路由正向枚举，而不是仅靠 import 文本反推。

### 4.4 可信旁路的范围（已按最终需求更正）

实施前版本一度把可信旁路限制为非安全方法。最终需求明确为 Nexus 判权后的全部请求均可旁路 Plane 原生角色判断，因此该限制已取消。

当前 permission 旁路统一检查：

```python
getattr(request, "is_trusted_nexus_call", False)
```

GET、HEAD、OPTIONS 与写请求采用相同的签名、请求绑定、防重放及限流校验；通过后可绕过 membership、Guest 和私有资源的角色判断。资源存在性、请求参数和 serializer 等业务约束仍保留。

## 5. 令牌设计需要补齐的安全边界

### 5.1 method 和 path 不足以绑定具体操作

如果 JWT 只包含 actor、workspace、method、path 和过期时间，持有者仍可以在有效期内替换同一 endpoint 的请求 body。例如，获批创建普通 work item 的令牌可以被用于同一路径创建不同内容。

若目标是“令牌只代表 Nexus 已经批准的这一次具体写操作”，签名声明至少应包含：

- `actor_plane_id`
- HTTP method
- 规范化后的 path
- 请求 body 的摘要，例如 SHA-256
- `iat`
- `exp`
- 唯一 `jti`
- issuer 和 audience

Plane 必须在认证阶段比较真实请求的 method、path 和 body digest，全部一致后才能设置 `request.is_trusted_nexus_call = True`。

需要提前规定 body 规范化方式。直接对原始字节计算摘要最简单，但要求 Nexus 签名后不得重新序列化 JSON；如果使用 canonical JSON，则两端必须采用完全一致的规范化算法。

### 5.2 短时 JWT 不等于一次性令牌

60 秒过期只缩短重放窗口，不能阻止相同 JWT 在 60 秒内重复执行同一 POST。

如果测试和文档使用“一次性令牌”这个术语，就必须让 Plane 使用 Redis 或数据库原子消费 `jti`。否则应把设计准确描述为“短时、操作绑定令牌”，并接受有效期内同操作可重放的风险。

### 5.3 用户解析需要明确失败条件

内部认证器通过 `actor_plane_id` 查询真实 Plane 用户时，应至少拒绝：

- 用户不存在；
- 用户被停用；
- actor ID 格式非法；
- issuer 或 audience 不匹配；
- 签名算法不在显式白名单中。

仓库已经依赖 `PyJWT==2.12.0`，不需要为基础 JWT 校验新增库。若采用非对称签名，Plane 只保存 Nexus 公钥，可降低 Plane 侧密钥泄露后伪造令牌的风险。

## 6. 网络隔离结论需要修正

当前 Caddy 配置是：

```caddy
reverse_proxy /api/* api:8000
```

因此公网 `/api/v1/...` 与 Compose 内网 `http://api:8000/api/v1/...` 最终进入同一个 Django 服务和同一认证器。

让 Nexus 主动选择内网地址，只能避免 Nexus 自己绕经公网，不能阻止外部请求把可信 Header 发送到公网 API。攻击者如果获得签名密钥或尚未过期的令牌，仍能通过公网入口调用可信认证。

上线方案至少需要满足以下一种隔离方式：

1. 公网 Caddy 明确拒绝或删除内部可信 Header；
2. 为可信调用提供单独的内部 URL 或监听端口，公网 proxy 不转发；
3. 配合容器网络策略、防火墙或 mTLS 限制调用来源。

较稳妥的组合是“独立内部入口 + 公网剥离 Header + 非对称签名”。不能把“调用方使用内网 DNS”本身视为服务端安全边界。

## 7. 审计归因的例外

compat work-item 的常见写操作确实把 `request.user.id` 作为 actor。但原生 `/api/v1/` 中仍有允许请求体影响 actor 的路径。

创建 work-item link 时：

```python
link.created_by_id = request.data.get("created_by", request.user.id)
actor_id = link.created_by_id
```

创建 comment 时：

```python
issue_comment.created_by_id = request.data.get("created_by", request.user.id)
issue_comment.actor_id = request.data.get("created_by", request.user.id)
```

这意味着调用者可以通过 `created_by` 改变部分活动记录的 actor。即使这是为数据迁移保留的现有 API 行为，也不能据此断言“内部认证设置 request.user 后，所有审计自然都是真实 actor”。

可信调用应至少采用一种措施：

- 对可信请求删除或拒绝 `created_by`、`updated_by`、`actor` 等归因字段；或
- 明确把“代填历史创建者”设计成另一种受限 action，并把真实执行者作为独立审计字段保存。

端到端测试必须覆盖 comment/link 等允许归因字段的接口，确认普通可信操作无法伪造 actor。

## 8. 建议的修订实施顺序

### Step 0：建立路由和权限覆盖测试

先通过 Django URL resolver 枚举全部 `/api/v1/` 路由，记录：

- 实际 view class；
- 来源是 `plane.api` 还是 compat 继承的 `plane.app`；
- authentication classes；
- permission classes；
- 写方法是否还有 `allow_permission` 或方法体内的显式 membership/owner 判断。

这份测试和清单应先于权限重构，作为后续改动的覆盖基线。

### Step 1：消除三组 permission 重复

仅合并或重新导出 `project.py`、`workspace.py`、`page.py`。不要把不同实现的 `base.py` 机械合并。

测试两个 import 路径导出的各权限类对象为同一个对象，并运行现有 API、app 和 contract 测试。

### Step 2：确定可信令牌协议

评审并冻结：

- Header 名称；
- 签名算法和密钥轮换；
- issuer、audience；
- actor、method、path、body digest、`jti` 的声明格式；
- 时钟偏差；
- 是否真正防重放；
- 混合 API Key 和内部凭据时的处理规则。

### Step 3：实现内部认证并覆盖所有 `/api/v1/` 入口

实现 `InternalServiceAuthentication`，然后同步接入：

- `plane.api.views.base.BaseAPIView`
- `plane.api.views.base.BaseViewSet`
- `plane.api.views.compat` 的全部显式认证配置

认证器只有在签名、时效、actor、method、path、body 和 replay 校验全部成功后，才设置可信请求标记。

同时决定内部调用的 throttle 策略。当前 API throttle 以 `X-Api-Key` 为 key；不带 API Key 的内部请求不会被该 throttle 限制。

### Step 4：覆盖两层角色判断

为全部 HTTP 方法增加可信旁路：

- `plane.app.permissions` 中实际进入 `/api/v1/` 的 permission classes；
- `plane.app.permissions.base.allow_permission`；
- 合并前仍被 API 使用的 utils permission import 路径。

安全方法和非安全方法都旁路角色判断，但不自动跳过资源存在性、serializer 校验、跨 workspace/project 一致性和其他业务规则。

### Step 5：加固审计归因

检查全部可信写路由中 `created_by`、`updated_by`、`actor` 和 activity actor 的来源。可信常规操作必须以令牌 actor 为真实执行者，不能由请求体覆盖。

### Step 6：建立服务端网络隔离

在部署层增加独立内部入口或公网 Header 剥离，并验证公网无法触发内部认证。随后再让 Nexus REST 代理切换到 Compose 内网地址。

### Step 7：端到端和回归测试

测试至少包括：

- 原有 API Key 请求行为不变；
- Guest 使用合法可信令牌执行写操作成功；
- 未带可信令牌的同一 Guest 写操作仍被拒绝；
- 签名错误、过期、actor 不存在或停用时拒绝；
- method、path、workspace、body digest 不匹配时分别拒绝；
- `jti` 重放被拒绝，或明确验证当前设计允许短时重放；
- 同时提交 API Key 和内部令牌时按规定处理；
- compat work-item 的 create、patch、delete 能通过装饰器层；
- cycle、module、state、page 等 compat 写路由至少各覆盖一个代表操作；
- invite/member 等 `BaseViewSet` 和 utils permission 路径有覆盖；
- 无 Plane membership 的 actor 使用可信 GET 可读取目标 workspace 资源，同一请求使用普通 API Key 仍被拒绝；
- comment/link 请求不能通过 `created_by` 伪造审计 actor；
- Plane 活动记录、webhook actor 与 Nexus 审计中的 actor 一致；
- 公网入口携带可信 Header 时被拒绝或 Header 被剥离。

## 9. 暂时无法在本仓库核实的内容

原计划引用的以下内容不在当前工作区中：

- `docs/权限修改-讨论要点.md`
- `nexus/auth.py::auth_internal_api_key`
- `db/rbac_store.py::record_audit_event`
- Nexus REST proxy 和 `plane-mcp-server` 的实际部署源码

因此，本报告可以确认 Plane fork 的 URL 路由和兼容层与“外部客户端使用 `/api/v1/`”一致，但无法仅凭当前仓库独立核实 Nexus 当前如何签发凭据、如何代理请求、如何记录审计，以及它是否与 Plane API 共享 Compose 网络。

## 10. 最终判断

建议保留原计划的目标架构，但在编码前先修正以下四项阻断问题：

1. 认证覆盖必须包含 `BaseAPIView`、`BaseViewSet` 和全部 compat 包装类；
2. 权限旁路必须同时覆盖 permission classes 与继承的 `allow_permission`；
3. 可信令牌必须绑定 body，并明确是否通过 `jti` 防重放；
4. 公网入口必须在服务端拒绝可信 Header，不能只依赖 Nexus 主动走内网。

完成这些修订后，再进行权限文件去重、认证实现、审计加固和灰度上线，风险会明显可控。

## 11. Plane 端实施复核（2026-09-09）

本报告前 10 节记录的是实施前核实结论。当前分支 `feat/plane-trusted-service-channel` 已完成 Plane 端实现；Nexus 签发和代理逻辑仍未修改。

部署、签发和调用操作请参见 `docs/plane-信任通道-使用说明和流程.md`。

### 11.1 已冻结的可信令牌协议

- Header：`X-Nexus-Trust-Token`；同时出现 `X-Api-Key` 时直接拒绝。
- 算法：只允许 `RS256`。Plane 仅配置当前公钥，并可在轮换窗口同时配置上一把公钥。
- 必填 claims：`iss`、`aud`、`actor_plane_id`、`method`、`path`、`query`、`workspace_slug`、`body_sha256`、`iat`、`exp`、`jti`。
- `path` 使用 Django 收到的原始 request path；`query` 使用未解码、未重排的原始 query string；`body_sha256` 对 HTTP 原始 body 字节计算 SHA-256。Nexus 签名后不得重新序列化请求体或调整查询参数顺序。workspace 路由的 `workspace_slug` 必须精确匹配；不含 workspace 的 `/api/v1/` 路由使用空字符串。
- 默认 issuer/audience 为 `nexus`/`plane`，最大令牌生命周期 60 秒，允许 5 秒时钟偏差。
- 允许所有 HTTP 方法；GET、HEAD、OPTIONS 与写请求一样必须通过完整的签名和请求绑定校验。
- `jti` 通过 Django cache/Redis 原子 `add` 一次性消费。缓存不可用时认证失败，不降级为可重放模式。
- 可信请求按 `actor_plane_id` 独立限流，默认 `600/minute`。

### 11.2 已完成的 Plane 改动

1. `InternalServiceAuthentication` 已接入 `BaseAPIView`、`BaseViewSet` 和全部 compat 包装类；URL resolver 测试正向枚举 `/api/v1/` 路由并检查认证与限流覆盖。
2. 认证器校验签名、算法、issuer/audience、时效、actor UUID/启用状态、method/path/raw query/workspace/raw body hash、project-workspace 归属和 replay，全部通过后才写入可信标记。
3. `permission_classes`、继承自 `plane.app` 的 `allow_permission`，以及 cycle/module/intake/work-item/attachment/page/view 中的内嵌角色或 owner 判断，都对已认证的可信请求旁路，覆盖读写方法。serializer、资源存在性、归档状态、默认状态、跨 workspace/project 一致性等业务约束保留。
4. `plane.utils.permissions` 的 project/workspace/page 模块改为重导出 `plane.app.permissions` 的规范类；测试确认两个 import 路径得到同一类对象。
5. 可信 JSON、表单和 multipart 请求中只要任意层出现 `actor`、`actor_id`、`created_by`、`created_by_id`、`updated_by`、`updated_by_id`，认证即失败，防止覆盖审计归因。
6. CE 与 AIO Caddy 公网 `/api/*` 入口都删除 `X-Nexus-Trust-Token`；可信请求只能直达私有 API listener。
7. `.env.example` 已加入公钥、issuer/audience、TTL、时钟偏差和 actor 限流配置。未配置公钥时通道默认关闭并 fail closed。

### 11.3 验证结果

- 新增认证、permission 重导出、resolver 覆盖和限流单元测试：`52 passed`。
- 可信通道合约测试：`8 passed`；与上述单元测试合并运行共 `60 passed`。新增覆盖无 workspace membership 的 actor：普通 API Key GET 为 403，可信 GET 为 200 并返回目标项目；同时保留 Guest 的 work-item create/delete，以及 cycle、module、state、page、BaseViewSet invitation 写入覆盖。
- work-item 兼容回归测试单独运行：`16 passed`。创建记录的 `created_by` 仍为令牌 actor。
- Ruff lint：全部受影响的 Plane Python 文件通过；核心实现及新增测试通过 Ruff format check。
- Django system check：`0 issues`。
- CE/AIO 两份 Caddy 配置均通过 `caddy validate`；适配后的 JSON 明确显示删除 `X-Nexus-Trust-Token`。
- 已执行 `docker compose build api` 和 `docker compose up -d api`；新 API 镜像构建成功，容器内生产设置 `manage.py check` 为 `0 issues`。
- 全部 API contract 基线曾运行到 `75 passed`，其余失败来自既有 cycle contract 预期、page/project-member 测试 fixture 和顺序时间戳等非本次可信通道路径；其中本次触及的 work-item contract 已修正并单独达到 `16 passed`。
- 根目录 `pnpm check` 未执行成功，因为工作区未安装 `node_modules`，`turbo` 不存在。本次没有修改 TypeScript/前端源码。

### 11.4 Plane 上线前仍需完成

- 生成 Nexus RS256 私钥/公钥，将公钥配置到 Plane，私钥只保留在 Nexus。
- 确认 Nexus 与 Plane 私有 API listener 的网络连通性；Nexus 不能经公网 Caddy 发送可信 Header，因为该 Header 会被剥离。
- 在部署环境配置公钥后，用当地 workspace/project/work-item ID 做一次私有入口直连验证。
- Nexus 端完成签发、原始字节转发和审计对账前，不应在生产启用该通道。
