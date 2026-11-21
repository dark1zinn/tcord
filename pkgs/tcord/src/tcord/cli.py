import logging
from pathlib import Path

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
@click.option(
    "--config",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Settings file to use instead of the default location.",
)
@click.option("--debug", is_flag=True, help="Write a debug log to the user log directory.")
@click.version_option(package_name="tcord")
def main(config: Path | None, debug: bool) -> None:
    """Tcord: a keyboard-first Discord client for the terminal."""
    if debug:
        _enable_debug_log()
    from . import i18n
    from .settings import SettingsFile, default_path

    settings_file = SettingsFile(default_path(config))
    settings, warnings = settings_file.load()
    i18n.setup(settings.general.language)

    from client import TcordClient
    from tcord_core import Session

    from .app import TcordApp

    session = Session(
        credential_store="system" if settings.general.remember_session else "memory",
        locale=i18n.engine_locale(),
        # Avatars, emoji and attachments (at most 256 MB, oldest files evicted first).
        cache_dir=platformdirs.user_cache_path("tcord") / "media",
    )
    TcordApp(TcordClient(session), settings, settings_file, warnings).run()
