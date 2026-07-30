import argparse
from pathlib import Path
from pyalexalist.setup_alexa_cli import download, list_versions

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download the alexa-cookie-cli binary.")
    parser.add_argument(
        "--version", "-v",
        metavar="VERSION",
        help="release version to download (e.g. 5.0.1); defaults to latest",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="list all available release versions and exit",
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="re-download even if binary already exists",
    )
    args = parser.parse_args()

    _binary_dir = Path(__file__).parent.parent / "alexa-cookie-cli"

    if args.list:
        list_versions()
    else:
        download(version=args.version, force=args.force, binary_dir=_binary_dir)
