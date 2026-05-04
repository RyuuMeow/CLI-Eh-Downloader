"""CLI entry point for GoEHentai."""

from __future__ import annotations

import logging
import sys

from .config import load_config
from .shell import Shell


def _setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.WARNING

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

    logging.basicConfig(
        level=level,
        handlers=[handler],
    )
    # Quiet down noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("hpack").setLevel(logging.WARNING)
    logging.getLogger("h2").setLevel(logging.WARNING)


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
            urls.append(args[i])
            i += 1

    _setup_logging(verbose)

    config = load_config(config_path)
    shell = Shell(config)

    if urls:
        # Non-interactive mode: add URLs, wait for completion, then exit
        for url in urls:
            shell._cmd_add(url)
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
        "GoEHentai — CLI Gallery Downloader\n"
        "\n"
        "Usage:\n"
        "  goeh                          Start interactive shell\n"
        "  goeh <url> [url2] ...         Download galleries and exit\n"
        "  goeh --config <path>          Use a custom config file\n"
        "  goeh --verbose / -v           Show detailed log messages\n"
        "  goeh --help                   Show this help\n"
        "\n"
        "Interactive commands:\n"
        "  add <url>   Add a download task\n"
        "  status      Show task progress\n"
        "  cancel <id> Cancel a task\n"
        "  config show Show configuration\n"
        "  help        Show all commands\n"
        "  quit        Exit\n"
    )


if __name__ == "__main__":
    main()
