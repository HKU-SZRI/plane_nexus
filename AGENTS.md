# Agent Development Guide

## Commands

- `pnpm dev` - Start all dev servers (web:3000, admin:3001)
- `pnpm build` - Build all packages and apps
- `pnpm check` - Run all checks (format, lint, types)
- `pnpm check:lint` - OxLint across all packages
- `pnpm check:types` - TypeScript type checking
- `pnpm fix` - Auto-fix format and lint issues
- `pnpm turbo run <command> --filter=<package>` - Target specific package/app
- `pnpm --filter=@plane/ui storybook` - Start Storybook on port 6006

## Code Style

- **Imports**: Use `workspace:*` for internal packages, `catalog:` for external deps
- **TypeScript**: Strict mode enabled, all files must be typed
- **Formatting**: oxfmt, run `pnpm fix:format`
- **Linting**: OxLint with shared `.oxlintrc.json` config
- **Naming**: camelCase for variables/functions, PascalCase for components/types
- **Error Handling**: Use try-catch with proper error types, log errors appropriately
- **State Management**: MobX stores in `packages/shared-state`, reactive patterns
- **Testing**: All features require unit tests, use existing test framework per package
- **Components**: Build in `@plane/ui` with Storybook for isolated development

## API Compatibility

- `/api/` is Plane's internal web API. Its views live under `plane.app`, use session authentication, and define the response contract consumed by the Plane web app.
- `/api/v1/` is the external API-Key API. When Nexus needs an internal `/api/` endpoint through `/api/v1/`, add a thin wrapper in `apps/api/plane/api/views/compat.py` that subclasses the corresponding `plane.app` view and replaces its authentication with `APIKeyAuthentication`.
- Register the compatible endpoint under `apps/api/plane/api/urls/`. Preserve the `/api/` response body exactly unless an existing documented V1 contract explicitly requires a shim.
- Current issue compatibility wrappers include description versions, issue relations, sub-issues, issue links, and identifier-based work-item details with `issue_reactions`, `issue_attachments`, `issue_link`, and `parent` expansions. The relation, sub-issue, and issue-link wrappers also expose the internal POST contracts through `/api/v1/`.
- Add contract coverage that requests `/api/` and `/api/v1/` as the same user and compares status codes and normalized JSON responses.
- Also verify the V1 endpoint through Nexus's local generic proxy: the Nexus request path is `/plane-api/` plus the complete `api/v1/...` Plane path. Compare the proxied JSON with a direct local Plane request.
- Use the local workspace, project, and work-item identifiers discovered through the running Nexus proxy. Do not reuse production or POC identifiers for local verification.
- The production Compose API service copies source into its image. After compatibility changes, run `docker compose build api` and then `docker compose up -d api` before live verification.
