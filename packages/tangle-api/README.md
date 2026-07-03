# tangle-api

Checked-in generated Tangle API models, operation proxies, and schema snapshot used by the default `tangle-cli` install.

This package is intentionally a leaf package: it depends on Pydantic, but not on `tangle-cli`. Custom API consumers can provide their own compatible distribution named `tangle-api` or a project-local `src/tangle_api` package that shadows the official package in that project environment.
