"""Vulture allowlist — names the dead-code gate must treat as used.

`harness deadcode` runs vulture over the app sources (`src/`) only, so code that
exists solely to satisfy a test is reported rather than hidden. Vulture cannot see
references made dynamically — decorator-registered handlers, getattr dispatch,
framework callbacks, serialization hooks. When vulture flags such a name, add it
here (for example `handler.on_event` or `_.middleware`) so the gate stays green
without scattering suppressions through the source. Empty by default.
"""
