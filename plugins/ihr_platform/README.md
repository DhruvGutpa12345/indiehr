# ihr_platform

IndieHR's platform layer on top of CARE (TENANCY_DESIGN.md, ADR-002 Amendment 1).

- `ihr_platform.models`: `Hospital` (the tenant: one legal entity, Q-T0) and `Domain`, stored in the shared `public` schema.
- `ihr_platform.settings.*`: settings overlays. Each imports CARE's settings, then calls `apply_tenancy()`. CARE's own settings files are not edited.
- `ihr_create_tenant`: creates a tenant schema, runs every migration in it and syncs CARE's roles and value sets (no template cloning in v1).
- `ihr_platform.test_runner.TenantTestRunner`: runs CARE's test suite inside a tenant schema.

Not installed with pip yet: dev and CI add `plugins/ihr_platform` to `PYTHONPATH`.
