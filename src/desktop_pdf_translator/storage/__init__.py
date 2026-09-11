"""The local data layer's shared plumbing: SQLite connections and migrations.

Stdlib-only, and re-exports nothing: this file runs whenever a submodule is
imported, and the stores that use it sit on the sidecar's boot path (see
"Import cost is a startup budget" in CLAUDE.md). Import `storage.sqlite` or
`storage.migrations` directly.
"""
