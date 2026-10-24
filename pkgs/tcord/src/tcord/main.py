import sys

import click
from client import TcordClient
from tcord_core import Session
from tui import Tui


@click.group(invoke_without_command=True)
@click.pass_context
@click.option("--debug", is_flag=True)
def main(ctx: click.Context, debug: bool) -> None:
    if debug:
        print(f"argc: {len(sys.argv)}\nargv: {sys.argv}")
    if ctx.invoked_subcommand is None:
        Tui(TcordClient(Session())).run()


@main.command()
@click.argument("url")
def join(url: str) -> None:
    raise click.ClickException(
        f"Joining servers is not available in the base chat milestone: {url}"
    )
