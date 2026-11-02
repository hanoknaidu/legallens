"""Phase 2 identity: passwords, sessions, sign-in throttling.

The BRD planned for this (§6.1): every table already carries `owner_id`, every query
already filters on it, and `deps.current_owner_id` was left as the single seam. This
package fills the seam in. Nothing here changes the schema of existing tables.
"""
