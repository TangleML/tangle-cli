import typer

app = typer.Typer(no_args_is_help=True)

pipeline_runs_app = typer.Typer(no_args_is_help=True)
app.add_typer(pipeline_runs_app, name="pipeline-runs", no_args_is_help=True)

execution_nodes_app = typer.Typer(no_args_is_help=True)
app.add_typer(execution_nodes_app, name="execution-nodes", no_args_is_help=True)

artifacts_app = typer.Typer(no_args_is_help=True)
app.add_typer(artifacts_app, name="artifacts", no_args_is_help=True)

published_components_app = typer.Typer(no_args_is_help=True)
app.add_typer(published_components_app, name="published-components", no_args_is_help=True)
