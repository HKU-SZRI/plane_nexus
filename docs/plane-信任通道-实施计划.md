# Plane 端实施计划：接入 Nexus 可信服务通道（v3，全请求可信旁路）

> 2026-09-09 范围更正：可信通道应承载 Nexus 已判权的全部请求，而不只是写请求。v2 中“安全方法继续执行 Plane 原生权限”的约束已作废。

承接 `docs/权限修改-讨论要点.md` 第 8 节的目标架构。v1 版本被 `docs/plane-信任通道-源码核实报告.md` 核出 4 个阻断性问题——已抽查其中最关键的三条(`compat.py` 继承链、`BaseViewSet` 独立于 `BaseAPIView`、`created_by` 字段可覆盖审计归因),全部属实,而且 `created_by` 覆盖的范围比报告举的例子更大(不只是 link/comment,`issue.py` 里 work item 创建本身也有)。本版本按核实结果重写,v1 的判断已作废,不要再参照。

## 0. v1 里被推翻/修正的关键假设

- ❌ **"只改 `BaseAPIView` 就能覆盖 `/api/v1/`"**——实际还有一个独立的 `BaseViewSet`(`plane/api/views/base.py:167`,`WorkspaceInvitationsViewset`、`StickyViewSet` 等用它),父类改了不会覆盖它;更大的遗漏是 `plane/api/views/compat.py` 里 **36 处**显式声明 `authentication_classes = [APIKeyAuthentication]`,显式声明不会被父类默认值覆盖。
- ❌ **"`allow_permission` 装饰器不影响 `/api/v1/`,因为 `plane/api/views/*.py` 里搜不到它"**——这条结论的检查方法本身有问题:只搜了"这个文件里有没有直接写 `@allow_permission`",没搜"这个文件里的类是不是继承自一个被 `@allow_permission` 装饰过的类"。实测确认:`compat.py` 里 `IssueV1ViewSet(_IssueViewSet)`,而 `_IssueViewSet = plane.app.views.issue.base.IssueViewSet`——直接 import 并继承 Plane 网页版用的同一个类,继承下来的 `create`/`partial_update`/`destroy` 方法上挂着 `@allow_permission`,会在 `/api/v1/` 上原样执行。`cycle`、`module`、`page`、`state` 等 compat 视图是同样的继承模式。work-item、cycle、module、page、state、关系、订阅、附件——这些恰好是 Nexus 最常用的写路由,不是边缘情况。
- ⚠️ **"改完 permission 类 + `request.user` 归因就能保证审计是真人"**——不成立。`plane/api/views/issue.py` 里至少 5 处允许请求体覆盖归因字段:
  ```python
  # 472, 690 行:issue 创建本身
  issue.created_by_id = request.data.get("created_by", request.user.id)
  # 1170 行:link 创建
  link.created_by_id = request.data.get("created_by", request.user.id)
  # 1455-1456 行:comment 创建
  issue_comment.created_by_id = request.data.get("created_by", request.user.id)
  issue_comment.actor_id = request.data.get("created_by", request.user.id)
  ```
  一次被 Nexus 批准的可信写请求,如果不显式剥离/拒绝这个字段,调用方可以顺手把审计里的 actor 伪造成任意人——这比"信任通道跳过角色检查"本身更隐蔽,因为它连"记录了谁做的"这道最后防线都能绕开。
- ⚠️ **permission import 清单不完整,取样方法本身有问题**:少了 `estimate.py`(实际 9 个文件,不是 v1 说的 8 个),而且这是从"哪些文件 import 了这几个类"反推的,反推不到 `compat.py` 的间接继承。正确做法是用 Django URL resolver 正向枚举 `/api/v1/` 全部路由,反查每条路由实际挂的 view class、认证类、权限类、有没有继承 `allow_permission`。
- ⚠️ **"用 Compose 内网地址调用 = 网络隔离"不成立**——查了 Caddy 配置,`/api/*` 统一 `reverse_proxy` 到同一个 `api:8000`,不管请求是从内网 DNS 还是公网域名进来,最终打的是同一个 Django 服务、同一套认证器。这只能保证 Nexus 自己不主动走公网,挡不住别人拿到令牌/密钥后直接从公网发起可信调用。真正的隔离要么是公网入口(Caddy)显式剥离/拒绝可信 Header,要么是给可信调用单独开一个不对公网暴露的监听端口,二选一或都做。
- ⚠️ **令牌设计要补两块**:①只签 method+path+exp 挡不住"有效期内换个 body 重放到同一个 endpoint",要加请求体摘要(hash)+ `jti` + issuer/audience;②"短时"不等于"一次性"——60 秒过期只缩短重放窗口,不接 Redis/DB 做 `jti` 消费记录的话,同一个 JWT 在有效期内依然能被重复提交,文档措辞要么补上防重放机制,要么如实改成"短时、绑定操作,有效期内可重放"。

## 1. 目标(不变)

Nexus 判权通过之后，能对 `/api/v1/` 发起一次读或写请求，让 Plane 认得这是“已经被 Nexus 批准的请求”，跳过原生角色判断（包括 permission classes 和 `allow_permission` 装饰器两层）。写请求的活动记录/审计仍显示真实操作人，且不可被请求体伪造。

## 2. 修订后的实施顺序

### Step 0:建立路由和权限覆盖测试(新增,提到最前面)

用 Django URL resolver 枚举全部 `/api/v1/` 路由,对每条路由记录:实际 view class、来源是 `plane.api` 原生还是 `compat` 继承自 `plane.app`、authentication classes、permission classes、写方法上是否还有 `allow_permission` 或方法体内显式的 membership/owner 判断。这份清单是后续所有改动的覆盖基线——v1 直接从 grep import 文本反推清单,漏了 `estimate.py` 和整条 compat 继承链,这次要反过来:先枚举真实运行时路由,再对着清单核对有没有漏改的地方。

### Step 1:消除 `app/permissions` 与 `utils/permissions` 的重复

`plane/app/permissions/{project,workspace,page}.py` 与 `plane/utils/permissions/{project,workspace,page}.py`(含两边的 `__init__.py`)逐字节相同,已用 `diff -rq` 核实;两边的 `base.py` **不同**(`app` 版本的 `allow_permission` 比 `utils` 版本多一段 workspace membership 检查),不能机械合并。

只合并 `project.py`/`workspace.py`/`page.py`:把 `utils` 版本改成从 `app` 版本重新导出。仓库内没找到依赖这两套类对象身份不同的 `isinstance`/`issubclass`/`is` 判断,但改完仍要跑一次全量回归测试(覆盖不到仓库外部调用者和动态导入)。验收:两个 import 路径导出的类是同一个对象,不只是逻辑等价。

### Step 2:确定可信令牌协议(评审后冻结,不在实现时顺手定)

评审并冻结：Header 名称；签名算法和密钥轮换方式（优先非对称签名，Plane 侧只存公钥）；issuer、audience；`actor_plane_id`/method/path/query/workspace_slug/请求体摘要（SHA-256）/`jti`/`iat`/`exp` 的声明格式；时钟偏差容忍度；是否真正防重放；同时携带 API Key 和内部令牌时的处理规则。workspace 路由签入实际 slug，不含 workspace 的 `/api/v1/` 路由签入空字符串。

请求体摘要的规范化方式要提前定死:直接对原始字节算摘要最简单,但要求 Nexus 签名后不能重新序列化 JSON;用 canonical JSON 的话两端算法必须完全一致。

### Step 3:实现 `InternalServiceAuthentication`,覆盖全部 `/api/v1/` 入口

不能只改 `BaseAPIView`。建一份共享认证配置(比如 `API_AUTHENTICATION_CLASSES = [APIKeyAuthentication, InternalServiceAuthentication]`),同步接入:

- `plane.api.views.base.BaseAPIView`
- `plane.api.views.base.BaseViewSet`
- `plane.api.views.compat` 里全部 36 处显式声明

混合凭据行为要显式定义:DRF 按顺序调用认证器——不带 `X-Api-Key` 时现有认证器返回 `None`、会继续往下试;带**无效** `X-Api-Key` 时现有认证器直接抛 `AuthenticationFailed`、不会继续试下一个;两种凭据都有效时前面的 API-Key 认证会先胜出、这次请求不会被标记为可信调用。建议显式拒绝同时携带两种凭据的请求,避免调用方误以为内部授权已经生效。

同时要定内部调用的 throttle 策略——当前 API throttle 以 `X-Api-Key` 为 key,不带 API Key 的内部请求不会被这个 throttle 限制,不能假设它自动被限流。

### Step 4：覆盖全部请求的两层角色判断

可信标记通过认证后，读写请求都跳过 Plane 的原生角色判断：

```python
if getattr(request, "is_trusted_nexus_call", False):
    return True  # 或 view_func(...)，取决于是 permission class 还是 allow_permission 装饰器
```

要覆盖的是两层,不是一层:
1. `plane.app.permissions` 中实际进入 `/api/v1/` 的 permission classes(Step 1 合并后的那一份)。
2. `plane.app.permissions.base.allow_permission` 装饰器——它在 `plane.app.views` 里有 38 个文件、195 处使用,通过 `compat.py` 的继承链影响 work-item/cycle/module/state 等 `/api/v1/` 写路由。装饰器内部要加和上面同样的可信标记 + 非安全方法判断。

可信标记只能由 Step 3 的认证器在全部令牌校验（签名、时效、actor、method、path、query、body digest、防重放）都通过后设置，不能由客户端提交的普通 Header 直接触发。可信旁路覆盖安全方法和非安全方法，但不跳过资源存在性、serializer 校验、跨 workspace/project 一致性等业务规则。

### Step 5:加固审计归因

检查全部可信写路由中 `created_by`、`updated_by`、`actor`、activity actor 的来源字段。已确认 `plane/api/views/issue.py` 里 issue 创建、link 创建、comment 创建都允许请求体的 `created_by` 覆盖 `request.user`——可信调用必须至少做到以下一种:①对可信请求直接删除/拒绝 `created_by`/`updated_by`/`actor` 等归因字段;②把"代填历史创建者"设计成单独的、更受限的 action,把真实执行者作为独立字段记录。不能假设"改完认证类,`request.user` 是真人"就自动等于"所有审计都是真人"——这两件事之间隔着这几处允许请求体覆盖的代码。

Nexus 侧 `db/rbac_store.py::record_audit_event` 同步记一份,两边对得上。

### Step 6:建立服务端网络隔离

不能只让 Nexus 主动选内网地址(Caddy 把公网和内网请求转发到同一个 `api:8000`,服务端分不出请求是从哪条路径来的)。至少满足以下一种:①公网 Caddy 层显式拒绝或剥离可信 Header;②给可信调用开一个公网 proxy 不转发的独立内部入口/端口;③配合容器网络策略、防火墙或 mTLS 限制调用来源。较稳妥的组合是"独立内部入口 + 公网剥离 Header + 非对称签名"三者一起,而不是依赖任意一种单独生效。这一步和上面几步同批上线,不然信任通道等于在公网裸奔。

### Step 7:端到端和回归测试

至少覆盖：原有 API Key 请求行为不变；无 Plane membership 的用户用合法可信令牌读取成功，同一读取使用普通 API Key 被拒绝；Guest 用合法可信令牌写操作成功，同一操作不带令牌被拒绝；签名错误/过期/actor 不存在或停用时拒绝；method/path/query/workspace/body digest 不匹配时分别拒绝；`jti` 重放被拒绝；同时提交 API Key 和内部令牌时拒绝；compat 的 work-item/cycle/module/state/page 写路由至少各覆盖一个代表操作；`invite`/`member` 等 `BaseViewSet` 和 utils permission 路径有覆盖；comment/link/issue 创建请求不能通过 `created_by` 伪造审计 actor；Plane 活动记录、webhook actor 与 Nexus 审计中的 actor 一致；公网入口携带可信 Header 时被拒绝或 Header 被剥离。

## 3. 灰度上线(顺序不变)

先只给 Nexus 的 REST 代理接入信任通道,MCP 继续走真实用户 key(对应 `docs/权限修改-讨论要点.md` 第 8 节的阶段划分),观察一段时间的拒绝日志和 Plane 活动记录,确认没有"该通过没通过"或"不该通过却通过了"的案例,再考虑要不要把 MCP 也接进来——前提是 Nexus 那边的 MCP guard 覆盖率已经验证完整。

## 4. 仍未展开、需要单独核实的点

- Nexus 侧(`nexus/auth.py::auth_internal_api_key`、`db/rbac_store.py::record_audit_event`、REST proxy 和 `plane-mcp-server` 的实际部署代码)不在 `plane_nexus` 仓库里,这份计划只能核实 Plane fork 这一侧;两边是否共享同一个 Compose 网络、Nexus 具体怎么签发和携带令牌,需要在 `nexus` 仓库里对应核实一遍。
- 令牌签发的密钥管理和轮换机制、Plane fork 版本升级时这些 patch 怎么随上游合并走(尤其是 Step 4 改的两层判断,一层在 permission classes 一层在装饰器,上游一旦重构其中任何一层,补丁都要重新核对),这次没有展开设计。
- `estimate.py` 补进 Step 0 的枚举清单后,建议顺带确认还有没有其他这次静态检查没扫到的文件——Step 0 的路由枚举测试就是为了兜住这类遗漏,而不是继续手工列清单。
