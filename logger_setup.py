import logging
import logging.handlers
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def listLogContext(
    list_name: str,
    log_dir: Path = Path("logs"),
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
    file_level: int = logging.DEBUG,
):
    """Adds a per-list rotating log file for the duration of one list pair's sync, then removes it."""
    log_dir.mkdir(exist_ok=True)
    safe_name = list_name.lower().replace(" ", "_").replace("/", "_")
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.handlers.RotatingFileHandler(
        log_dir / f"{safe_name}.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    fh.setLevel(file_level)
    fh.setFormatter(fmt)
    root = logging.getLogger()
    root.addHandler(fh)
    try:
        yield
    finally:
        root.removeHandler(fh)
        fh.close()


def setupLogging(
    log_dir: Path = Path("logs"),
    log_to_console: bool = True,
    log_to_file: bool = True,
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
):
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    logging.getLogger("gkeepapi").setLevel(logging.WARNING)

    if log_to_console:
        ch = logging.StreamHandler()
        ch.setLevel(console_level)
        ch.setFormatter(fmt)
        root.addHandler(ch)

    if log_to_file:
        log_dir.mkdir(exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_dir / "gkeepalexa.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        fh.setLevel(file_level)
        fh.setFormatter(fmt)
        root.addHandler(fh)
