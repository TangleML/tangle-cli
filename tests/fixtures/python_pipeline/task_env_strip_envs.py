"""Fixture: an authoring-only ``TaskEnv`` module imported by strip fixtures.

This sibling module is intentionally NOT packaged into the runtime image. A
baked operation that still ``import``s it (or references ``UPI``) would crash
with ``ImportError`` / ``NameError`` at container start -- exactly what the
TaskEnv runtime-strip hardening prevents.

Used by:
- ``task_env_strip_imported_op.py``    (``from task_env_strip_envs import UPI``)
- ``task_env_strip_module_op.py``      (``import task_env_strip_envs``)
- ``task_env_strip_mixed_import_op.py``(``from task_env_strip_envs import UPI, helper``)

``helper`` is a stand-in RUNTIME name used to exercise the mixed-import
fail-fast: an env-only name sharing an import statement with a runtime name.
"""

from tangle_deploy.python_pipeline import TaskEnv

UPI = TaskEnv(image="python:3.12")


def helper(value):
    """A runtime helper that (in a real project) would be packaged into the
    image — here only used to trip the mixed-import fail-fast."""
    return f"hi {value}"
