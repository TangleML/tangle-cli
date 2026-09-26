"""A ``None``-defaulted ``In[T]`` parameter resolves the same on every Python.

Python 3.10's ``typing.get_type_hints`` still applies PEP 484 implicit
``Optional``, which 3.11 removed. ``x: In[str] = None`` therefore resolved to
``In[str]`` on 3.11+ and ``Optional[In[str]]`` on 3.10, where the tracer then
rejected it as "not annotated In[T]". CI runs 3.12 and 3.13 while
``requires-python`` is ``>=3.10``, so nothing caught it.

These tests exercise the unwrapping directly, so they fail on 3.10 without the
fix and still pin the rule on the versions CI runs.
"""

from __future__ import annotations

import inspect
import textwrap
from pathlib import Path
from typing import Optional, Union

import yaml

from tangle_cli.pipeline_compiler import compile_pipeline
from tangle_cli.python_pipeline import In
from tangle_cli.python_pipeline.trace import _strip_optional_none

_PIPELINE = '''
from typing import Optional

from tangle_cli.python_pipeline import In, Out, pipeline, task


@task(image="python:3.12")
def greet(greeting: str = "hi"):
    """Write a greeting.

    Metadata:
        Name: Greet
    """
    print(greeting)


@pipeline("Optionals")
def optionals(__PARAMS__) -> Out[str]:
    run_greet = greet(greeting=b)
    return run_greet
'''


def _compile(tmp_path: Path, params: str, case: str, *, config: str | None = None) -> dict:
    case_dir = tmp_path / case
    case_dir.mkdir(parents=True, exist_ok=True)
    if config is not None:
        (case_dir / "config.yaml").write_text(config, encoding="utf-8")
    script = case_dir / "pipeline.py"
    script.write_text(
        textwrap.dedent(_PIPELINE).replace("__PARAMS__", params), encoding="utf-8"
    )
    out = case_dir / "compiled.yaml"
    compile_pipeline(script, out)
    return yaml.safe_load(out.read_text(encoding="utf-8"))


# ============================================================================
# The unwrapping rule
# ============================================================================


def test_an_optional_paired_with_a_none_default_is_unwrapped():
    """This is the shape 3.10 invents for ``x: In[str] = None``."""
    assert _strip_optional_none(Optional[In[str]], None) is In[str]


def test_an_optional_without_a_none_default_is_left_alone():
    """Only the implicit-Optional pairing is rewritten; an explicit Optional
    on a parameter with a real default, or none at all, keeps what the author
    wrote."""
    annotation = Optional[In[str]]

    assert _strip_optional_none(annotation, "x") is annotation
    assert _strip_optional_none(annotation, inspect.Parameter.empty) is annotation


def test_a_bare_annotation_is_left_alone():
    assert _strip_optional_none(In[str], None) is In[str]
    assert _strip_optional_none(str, None) is str


def test_a_wider_union_is_left_alone():
    """``Union[A, B, None]`` is a genuine union; unwrapping it would invent a
    type the author never wrote."""
    annotation = Union[In[str], In[int], None]

    assert _strip_optional_none(annotation, None) is annotation


# ============================================================================
# End to end
# ============================================================================


def test_a_none_defaulted_input_compiles(tmp_path):
    doc = _compile(tmp_path, 'a: In[str] = None, b: In[str] = "x"', "none_default")

    assert doc["inputs"] == [
        {"name": "a", "type": "String", "default": None, "optional": True},
        {"name": "b", "type": "String", "default": "x", "optional": True},
    ]


def test_an_explicit_optional_input_compiles_the_same_way(tmp_path):
    """3.10 and 3.11+ disagree about which of these two spellings you wrote,
    so both must land on the same document."""
    doc = _compile(
        tmp_path,
        'a: Optional[In[str]] = None, b: In[str] = "x"',
        "explicit_optional",
    )

    assert doc["inputs"][0] == {
        "name": "a",
        "type": "String",
        "default": None,
        "optional": True,
    }


def test_a_cfg_parameter_defaulting_to_none_is_still_the_config(tmp_path):
    """``cfg`` is detected by name plus 'not an In[T]'. On 3.10 the implicit
    Optional made that check read differently, so pin it here too."""
    doc = _compile(
        tmp_path, 'cfg=None, b: In[str] = "x"', "cfg_none", config="key: value\n"
    )

    assert [i["name"] for i in doc["inputs"]] == ["b"]
