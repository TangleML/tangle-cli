import cyclopts

from . import api_cli
from . import components_cli

app = cyclopts.App()

app.command(api_cli.app)
app.command(components_cli.app)


if __name__ == "__main__":
    app()
