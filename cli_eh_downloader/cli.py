"""CLI entry point for CLI-Eh-Downloader."""

from __future__ import annotations

import logging
import shlex
import sys

from .config import load_config
from .shell import Shell


class _PromptSafeStreamHandler(logging.StreamHandler):
    """Write through the current stdout so prompt-toolkit can redraw input."""

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stdout
        super().emit(record)


def _setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.WARNING

    handler = _PromptSafeStreamHandler()
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

    logging.basicConfig(
        level=level,
        handlers=[handler],
        force=True,
    )
    # Quiet down noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("hpack").setLevel(logging.WARNING)
    logging.getLogger("h2").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def main() -> None:
    """Main entry point — launches the interactive shell."""
    import colorama
    colorama.init()

    # Allow an optional --config flag
    config_path = None
    args = sys.argv[1:]

    # Simple arg parsing (avoid heavy dependency for just this)
    i = 0
    urls: list[str] = []
    verbose = False
    while i < len(args):
        if args[i] in ("--config", "-c") and i + 1 < len(args):
            config_path = args[i + 1]
            i += 2
        elif args[i] in ("--verbose", "-v"):
            verbose = True
            i += 1
        elif args[i] in ("--help", "-h"):
            _print_usage()
            return
        else:
            # Treat as a URL to download
            spec = args[i]
            if i + 2 < len(args) and args[i + 1] in ("-p", "--preset", "--save-preset"):
                spec = f"{shlex.quote(spec)} -p {shlex.quote(args[i + 2])}"
                i += 3
            else:
                i += 1
            urls.append(spec)

    config = load_config(config_path)
    _setup_logging(verbose or config.debug_mode)
    shell = Shell(config)

    if urls:
        # Non-interactive mode: add URLs, wait for completion, then exit
        for url in urls:
            shell._dispatch(url)
        # Show status and wait
        import time
        try:
            while shell.manager.get_active_tasks():
                time.sleep(2.0)
                shell._cmd_status()
        except KeyboardInterrupt:
            pass
        finally:
            shell._shutdown()
    else:
        # Interactive mode
        shell.run()


def _print_usage() -> None:
    print(
        "CLI-Eh-Downloader — CLI Gallery Downloader\n"
        "\n"
        "Usage:\n"
        "  ehdl                          Start interactive shell\n"
        "  ehdl <url> [-p preset] ...    Download galleries and exit\n"
        "  ehdl --config <path>          Use a custom config file\n"
        "  ehdl --verbose / -v           Show detailed log messages\n"
        "  ehdl --help                   Show this help\n"
        "\n"
        "Interactive commands:\n"
        "  add <url> [-p preset] Add a download task\n"
        "  status      Show task progress\n"
        "  cancel <id> Cancel a task\n"
        "  config show Show configuration\n"
        "  help        Show all commands\n"
        "  quit        Exit\n"
    )


if __name__ == "__main__":
    main()
