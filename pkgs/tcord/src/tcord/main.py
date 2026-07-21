import sys
import click

from tui import Tui

@click.group(invoke_without_command=True)
@click.pass_context
@click.option("--debug", is_flag=True)
def main(ctx, debug):
    # When passing options in front of a Just call it will try to pickup the option
    # thus returning an error since the option probably doesn't exist in the recipe
    # to mitigate that pass `--` before the option for the cli
    # Example: `just r -- --debug`
    # this is only necessary for options before any subcommand
    if debug:
        print(f"THIS IS MAIN:\n  argc: {len(sys.argv)}\n  argv: {sys.argv}\n")

    if ctx.invoked_subcommand is None:
        print("Hello tcord UI!")
        Tui().run()
        # Click does provides helpers for exiting the program, afaik
        # these should be applied instead of a sys.exit()
        sys.exit(0)
    
    # We can't exit here since other commands need to be processed
    # e.g. exiting here defeats a CLI purpose
    # sys.exit(0)

# This is adummy command for testing
@main.command()
@click.argument("url")
def join(url):
    print(f"Joining server at {url}")
    sys.exit(0)
