from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib

from pyalexalist.get_alexa_cookies import main

if __name__ == "__main__":
    _root = Path(__file__).parent.parent
    _config_path = _root / "config" / "lists_sync_config.toml"
    _amazon_domain = "amazon.com"
    if _config_path.exists():
        with open(_config_path, "rb") as _f:
            _amazon_domain = tomllib.load(_f).get("settings", {}).get("alexa", {}).get("amazon_domain", "amazon.com")

    main(
        service_auth_path=_root / "config" / "service_auth.json",
        runtime_creds_path=_root / "config" / "runtime_credentials.json",
        amazon_domain=_amazon_domain,
    )
