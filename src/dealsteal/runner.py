"""Command-line entry points for batch and watch-mode deal scanning."""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from .config import load_config, load_profiles, migrate_legacy_queries
from .scanner import MarketplaceScanner
from .storage import SQLiteStore

LOGGER = logging.getLogger(__name__)


def _default_paths() -> list[str]:
    return sorted(glob.glob(os.getenv("ITEM_QUERY_GLOB", "store/item_queries/*.json")))


def _paths(values: list[str] | None) -> list[str]:
    return values or _default_paths()


def _duration(value: str) -> int:
    value = value.strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if value[-1:] in units:
        return max(1, int(float(value[:-1]) * units[value[-1]]))
    return max(1, int(value))


def _json_print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, default=str))


def _add_profile_paths(command: argparse.ArgumentParser) -> None:
    command.add_argument("paths", nargs="*", help="profile/query JSON files")
    command.add_argument("--config", default=None, help="local scanner config JSON")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dealsteal")
    subcommands = parser.add_subparsers(dest="command")

    validate = subcommands.add_parser(
        "validate", help="validate profile and scanner configuration"
    )
    _add_profile_paths(validate)

    migrate = subcommands.add_parser(
        "migrate", help="convert legacy query JSON to version 2"
    )
    migrate.add_argument("paths", nargs="*", help="legacy query JSON files")
    migrate.add_argument(
        "--output", default=None, help="write migrated JSON to this path"
    )

    scan = subcommands.add_parser("scan", help="run one batch scan")
    _add_profile_paths(scan)
    scan.add_argument(
        "--jsonl", action="store_true", help="emit progress and results as JSONL"
    )
    scan.add_argument(
        "--deals-only", action="store_true", help="suppress non-deal events"
    )

    watch = subcommands.add_parser(
        "watch", help="repeat scans using persistent SQLite state"
    )
    _add_profile_paths(watch)
    watch.add_argument("--interval", default=None, help="override interval, e.g. 15m")
    watch.add_argument("--once", action="store_true", help="run one scan and exit")
    watch.add_argument(
        "--jsonl", action="store_true", help="emit progress and results as JSONL"
    )

    report = subcommands.add_parser("report", help="read recent stored scan results")
    report.add_argument("--config", default=None, help="local scanner config JSON")
    report.add_argument("--limit", type=int, default=100)
    report.add_argument("--deals-only", action="store_true")
    return parser


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        return


def _run_scan(args: argparse.Namespace) -> int:
    config = load_config(getattr(args, "config", None))
    paths = _paths(getattr(args, "paths", None))
    if not paths:
        LOGGER.error("No profile/query JSON files found")
        return 2
    profiles = load_profiles(paths)
    scanner = MarketplaceScanner(config)

    def emit(event: dict[str, Any]) -> None:
        if getattr(args, "deals_only", False) and event.get("event") != "deal":
            return
        if getattr(args, "jsonl", False):
            _json_print(event)

    result = scanner.scan(profiles, emit=emit)
    if getattr(args, "jsonl", False):
        _json_print({"event": "scan_complete", **result})
    elif getattr(args, "deals_only", False):
        _json_print(
            {
                "scan_id": result["scan_id"],
                "deals": result["deals"],
                "stats": result["stats"],
            }
        )
    else:
        _json_print(result)
    scanner.store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parser().parse_args(argv)
    command = args.command or "scan"
    if command == "validate":
        config = load_config(args.config)
        profiles = load_profiles(_paths(args.paths))
        _json_print(
            {
                "valid": True,
                "profiles": len(profiles),
                "deal_profiles": sum(bool(profile.tiers) for profile in profiles),
                "destination": config.destination.as_dict(),
                "marketplaces": list(config.marketplaces) or "all",
            }
        )
        return 0
    if command == "migrate":
        result = migrate_legacy_queries(_paths(args.paths))
        encoded = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(encoded + "\n", encoding="utf-8")
        else:
            print(encoded)
        return 0
    if command == "scan":
        return _run_scan(args)
    if command == "watch":
        while True:
            result = _run_scan(args)
            if args.once or result != 0:
                return result
            config = load_config(args.config)
            time.sleep(
                _duration(args.interval)
                if args.interval
                else config.watch_interval_seconds
            )
    if command == "report":
        config = load_config(args.config)
        store = SQLiteStore(config.database_path)
        if args.deals_only:
            _json_print({"deals": store.recent_deals(args.limit)})
        else:
            _json_print(
                {
                    "deals": store.recent_deals(args.limit),
                    "listings": store.recent_listings(args.limit),
                }
            )
        store.close()
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
