import logging

import click
import platformdirs


def _enable_debug_log() -> None:
    log_dir = platformdirs.user_log_path("tcord", ensure_exists=True)
    logging.basicConfig(
        filename=log_dir / "tcord.log",
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--debug", is_flag=True, help="Write a debug log to the user log directory.")
@click.version_option(package_name="tcord")
def main(debug: bool) -> None:
    """Tcord: a keyboard-first Discord client for the terminal."""
    if debug:
        _enable_debug_log()
    from .app import TcordApp

    TcordApp().run()
