"""Minimal compile fixture for a task argument sourced from a runtime secret."""

from tangle_cli.python_pipeline import dynamic_secret, pipeline, ref

MODEL = ref(name="model")


@pipeline(name="Dynamic Secret")
def dynamic_secret_pipeline() -> None:
    MODEL.named("Call Model")(openai_api_key=dynamic_secret("OPENAI_API_KEY"))
