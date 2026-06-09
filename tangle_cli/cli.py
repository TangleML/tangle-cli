from cyclopts import App

from . import api_cli
from . import components_cli

app = App(
    help="CLI for Tangle, the open-source ML pipeline orchestration platform.",
    version="0.0.1",
)

app.command(api_cli.app)
app.command(components_cli.app)


if __name__ == "__main__":
    app()
