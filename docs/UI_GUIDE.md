# TenantForge UI guide

## Design

The gateway's shared base template uses a modern dark workspace theme with violet accents, responsive spacing, visible keyboard focus, and reduced-motion support. Tailwind and the existing Jinja block structure remain in place.

## Landing page

The landing page explains tenant-scoped helpdesk workflows, links to existing signup/login routes and API docs, and provides a conceptual service-topology panel. The topology is informational, not a live health check.

## Existing routes and context preserved

- Signup: `/signup`
- Login: `/login`
- Authenticated dashboard: `/app`
- Logout form: `POST /logout`
- OpenAPI docs: `/docs`
- Existing `principal` context and role display remain referenced by the shared layout.

## Verification checklist

1. Follow the root README and start the services.
2. Verify signup, login/logout, dashboard, ticket search/filtering, status/priority changes, assignments, and comments.
3. Confirm tenant isolation tests still pass.
4. Check the API docs, narrow viewport layout, and keyboard focus.
5. Run the existing lint and test commands.

The UI update has not been runtime- or browser-tested as part of this change.
