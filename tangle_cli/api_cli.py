import cyclopts

app = cyclopts.App(name="api")

pipeline_runs_app = cyclopts.App(name="pipeline-runs")
app.command(pipeline_runs_app)

execution_nodes_app = cyclopts.App(name="execution-nodes")
app.command(execution_nodes_app)

artifacts_app = cyclopts.App(name="artifacts")
app.command(artifacts_app)

published_components_app = cyclopts.App(name="published-components")
app.command(published_components_app)
