"""Package marker so the shared helper has a package-qualified module identity.

With this file present the compiler derives the logical module namespace
``dedup_image_variants.shared_dbt`` from the source layout (it walks up while
``__init__.py`` exists). Without it the helper would still work, but its module
identity would be the bare ``shared_dbt``.
"""
