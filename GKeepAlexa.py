import atexit
import logging
import os
import time, timeit
try:
    import tomllib
except ImportError:
    import tomli as tomllib
from copy import deepcopy
from pathlib import Path

from requests.exceptions import ConnectionError as RequestsConnectionError

from GoogleKeepLists import GoogleKeepLists
from AlexaLists import AlexaLists
from list_structures import List, ListItem
from logger_setup import listLogContext

logger = logging.getLogger(__name__)

_CONFIG_PATH       = Path(__file__).parent / "config" / "lists_sync_config.toml"
_SERVICE_AUTH_PATH = Path(__file__).parent / "config" / "service_auth.json"
_LOCK_FILE         = Path(__file__).parent / "gkeepalexa.pid"


def _acquire_lock() -> None:
    if _LOCK_FILE.exists():
        try:
            pid = int(_LOCK_FILE.read_text().strip())
            os.kill(pid, 0)
            raise SystemExit(
                f"ERROR: Another GKeepAlexa instance appears to be running (PID {pid}).\n"
                f"       If this is stale, delete: {_LOCK_FILE}"
            )
        except (OSError, ValueError):
            pass  # stale lock — process gone or file unreadable
    _LOCK_FILE.write_text(str(os.getpid()))
    atexit.register(_release_lock)


def _release_lock() -> None:
    _LOCK_FILE.unlink(missing_ok=True)

with open(_CONFIG_PATH, "rb") as _f:
    _config = tomllib.load(_f)

_s = _config.get("settings", {})
CLEAR_ON_STARTUP       = _s.get("clear_on_startup", True)
CLEAR_ON_INTERVAL      = _s.get("clear_on_interval", True)
CLEAR_INTERVAL_SECONDS = _s.get("clear_interval_seconds", 3600)
MAX_ITERATIONS         = _s.get("max_iterations", 0)
SYNC_INTERVAL_SECONDS  = _s.get("sync_interval_seconds", 20)
GKEEP_PINNED_ONLY              = _s.get("gkeep", {}).get("pinned_only", True)
LIST_PAIRS                     = [p for p in _config["lists"] if p.get("enabled", True)]
GKEEP_DEFAULT_SORT             = _s.get("gkeep", {}).get("sort", "none").casefold()
GKEEP_SORT_MAP                 = {lst["gkeep"]:lst.get("gkeep_sort", GKEEP_DEFAULT_SORT).casefold() for lst in LIST_PAIRS}
GKEEP_NORMALIZE_QUANTITY_TEXT  = _s.get("gkeep", {}).get("normalize_quantity_text", True)
LOG_MAX_BYTES                = _s.get("log_max_bytes", 5 * 1024 * 1024)
LOG_BACKUP_COUNT             = _s.get("log_backup_count", 5)
ALEXA_COOKIE_EXPIRY_RETRIES  = _s.get("alexa", {}).get("cookie_expiry_retries", 0)
ALEXA_RETRY_INTERVAL_SECONDS = _s.get("alexa", {}).get("retry_interval_seconds", 30)
ALEXA_AMAZON_DOMAIN          = _s.get("alexa", {}).get("amazon_domain", "amazon.com")
MERGE_DUPLICATE_ITEMS        = _s.get("merge_duplicate_items", True)

_SYNC_INTERVAL_FLOOR = 5
if SYNC_INTERVAL_SECONDS < _SYNC_INTERVAL_FLOOR:
    import warnings
    warnings.warn(
        f"sync_interval_seconds={SYNC_INTERVAL_SECONDS} is below the minimum of {_SYNC_INTERVAL_FLOOR}s — clamping to {_SYNC_INTERVAL_FLOOR}s",
        stacklevel=1,
    )
    SYNC_INTERVAL_SECONDS = _SYNC_INTERVAL_FLOOR


class UpdateLists:
    """Orchestrates the bidirectional sync loop between Google Keep and Alexa lists."""

    def __init__(self) -> None:
        self.googleKeep = GoogleKeepLists(pinned_only=GKEEP_PINNED_ONLY, sort_map=GKEEP_SORT_MAP, normalize_quantity_text=GKEEP_NORMALIZE_QUANTITY_TEXT)
        self.Alexa = AlexaLists(cookie_expiry_retries=ALEXA_COOKIE_EXPIRY_RETRIES, retry_interval_seconds=ALEXA_RETRY_INTERVAL_SECONDS, amazon_domain=ALEXA_AMAZON_DOMAIN, service_auth_path=_SERVICE_AUTH_PATH)
        self.googleKeep.getCurrentListsItems(resync=True)
        self.Alexa.getCurrentListsItems()
        self.is_first_loop = True

    def updatingLists(self, max_count: int | float = float("inf")) -> None:
        """Run the sync loop indefinitely (or up to max_count iterations).

        Args:
            max_count: Maximum number of sync iterations; defaults to infinity.
        """
        count_n = 0
        t0_ = time.time()
        while max_count > count_n:
            start_time = timeit.default_timer()
            try:
                if not self.is_first_loop:
                    self.googleKeep.getCurrentListsItems()
                    self.Alexa.getCurrentListsItems()

                for pair in LIST_PAIRS:
                    with listLogContext(pair.get("name", pair["gkeep"]), max_bytes=LOG_MAX_BYTES, backup_count=LOG_BACKUP_COUNT):
                        a_list = deepcopy(self.Alexa.lists_and_items[pair["alexa"]])
                        g_list = deepcopy(self.googleKeep.lists_and_items[pair["gkeep"]])
                        if MERGE_DUPLICATE_ITEMS:
                            self._merge_duplicates(a_list, "Alexa")
                            self._merge_duplicates(g_list, "GKeep")
                        self.syncBins(a_list, g_list, self.is_first_loop)
                        logger.debug("[%s] item counts after syncBins — GKeep: %d, Alexa: %d",
                                     pair.get("name", pair["gkeep"]), len(g_list.items), len(a_list.items))
                        self.googleKeep.syncList(g_list)
                        self.Alexa.syncList(a_list)

                if self.is_first_loop and CLEAR_ON_STARTUP:
                    for pair in LIST_PAIRS:
                        self.Alexa.clearDoneCompleted(pair["alexa"])
                        self.googleKeep.clearDoneCompleted(pair["gkeep"])

                if CLEAR_ON_INTERVAL and time.time() > t0_ + CLEAR_INTERVAL_SECONDS:
                    for pair in LIST_PAIRS:
                        self.Alexa.clearDoneCompleted(pair["alexa"])
                        self.googleKeep.clearDoneCompleted(pair["gkeep"])
                    t0_ = time.time()
                    logger.info("Cleared completed items from all lists")

                elapsed = timeit.default_timer() - start_time
                count_n += 1
            except (RequestsConnectionError, ConnectionError) as e:
                logger.warning("Network error — skipping iteration, will retry: %s", e)
                elapsed = timeit.default_timer() - start_time
            finally:
                self.is_first_loop = False

            logger.debug("Sleeping %d seconds before next iteration", SYNC_INTERVAL_SECONDS)
            for i in range(SYNC_INTERVAL_SECONDS, 0, -1):
                print(f"Waiting for {i} seconds...", end="\r", flush=True)
                time.sleep(1)
            logger.info("Iteration #%d complete in %.2fs", count_n, elapsed)

    def _merge_duplicates(self, lst: List, side: str) -> None:
        """Collapse duplicate items (same itemIdentityKey) into one, summing explicit quantities.

        Keeps the most recently updated copy. If all copies have no quantity, the merged item
        also has no quantity. If any copy is unchecked, the merged item is unchecked.
        """
        groups: dict[str, list] = {}
        for item in lst.items:
            groups.setdefault(item.itemIdentityKey, []).append(item)
        for dupes in groups.values():
            if len(dupes) <= 1:
                continue
            primary = max(dupes, key=lambda i: i.updatedTime.timestamp() if i.updatedTime else 0)
            all_none = all(i.quantity is None for i in dupes)
            if all_none:
                merged_qty = None
            else:
                total = sum((i.quantity or 1) for i in dupes)
                merged_qty = min(total, 999) if total > 1 else None
            any_unchecked = any(not i.checked for i in dupes)
            logger.warning(
                "[MERGE DUPLICATES] %s '%s' — %d copies merged into qty=%s",
                side, primary.itemName, len(dupes), merged_qty,
            )
            for dupe in dupes:
                if dupe is not primary:
                    lst.remove(dupe)
            primary.quantity = merged_qty
            if any_unchecked:
                primary.checked = False

    def syncBins(self, alexa_bin: List, gkeep_bin: List, first_run: bool) -> None:
        """First run: match items by normalised name and assign shared IDs.
        Subsequent runs: match by shared ID and resolve conflicts by timestamp.

        Args:
            alexa_bin: Deep-copied Alexa List for this sync pass.
            gkeep_bin: Deep-copied GKeep List for this sync pass.
            first_run: If True, match items by identity key; otherwise match by shared ID.
        """
        alexa_bin.cascadeParentToChildren()
        if first_run:
            alexa_bin.id = gkeep_bin.id = List.generateId()

            all_keys = (
                {i.itemIdentityKey for i in alexa_bin.items} |
                {i.itemIdentityKey for i in gkeep_bin.items}
            )

            for key in all_keys:
                alexa_item = next((i for i in alexa_bin.items if i.itemIdentityKey == key), None)
                gkeep_item = next((i for i in gkeep_bin.items if i.itemIdentityKey == key), None)

                if not alexa_item and gkeep_item:
                    gkeep_item.id = ListItem.generateId()
                    _new_item = ListItem()
                    _new_item.itemName = gkeep_item.itemName
                    _new_item.id = gkeep_item.id
                    _new_item.parentItemName = gkeep_item.parentItemName
                    _new_item.checked = gkeep_item.checked
                    _new_item.quantity = gkeep_item.quantity
                    _new_item.createdTime = gkeep_item.createdTime
                    _new_item.updatedTime = gkeep_item.updatedTime
                    _new_item.resolvedTime = gkeep_item.updatedTime
                    _new_item.indented = gkeep_item.indented
                    if any(i.itemIdentityKey == _new_item.itemIdentityKey for i in alexa_bin.items):
                        logger.warning("[DUPLICATE SKIPPED] '%s' already in Alexa bin — not copying from GKeep", _new_item.itemName)
                    else:
                        alexa_bin.add(_new_item)
                elif not gkeep_item and alexa_item:
                    alexa_item.id = ListItem.generateId()
                    _new_item = ListItem()
                    _new_item.itemName = alexa_item.itemName
                    _new_item.id = alexa_item.id
                    _new_item.parentItemName = alexa_item.parentItemName
                    _new_item.checked = alexa_item.checked
                    _new_item.quantity = alexa_item.quantity
                    _new_item.createdTime = alexa_item.createdTime
                    _new_item.updatedTime = alexa_item.updatedTime
                    _new_item.resolvedTime = alexa_item.updatedTime
                    _new_item.indented = alexa_item.indented
                    if any(i.itemIdentityKey == _new_item.itemIdentityKey for i in gkeep_bin.items):
                        logger.warning("[DUPLICATE SKIPPED] '%s' already in GKeep bin — not copying from Alexa", _new_item.itemName)
                    else:
                        gkeep_bin.add(_new_item)
                elif alexa_item and gkeep_item:
                    alexa_item.id = gkeep_item.id = ListItem.generateId()
                    if not alexa_item == gkeep_item:
                        if alexa_item > gkeep_item:
                            gkeep_item.itemName = alexa_item.itemName
                            gkeep_item.checked = alexa_item.checked
                            gkeep_item.quantity = alexa_item.quantity
                            gkeep_item.createdTime = alexa_item.createdTime
                            gkeep_item.updatedTime = alexa_item.updatedTime
                            gkeep_item.resolvedTime = alexa_item.updatedTime
                            if not gkeep_item.isParent:
                                gkeep_item.indented = alexa_item.indented
                                gkeep_item.parentId = alexa_item.parentId
                                gkeep_item.parentItemName = alexa_item.parentItemName
                        else:
                            alexa_item.itemName = gkeep_item.itemName
                            alexa_item.checked = gkeep_item.checked
                            alexa_item.quantity = gkeep_item.quantity
                            alexa_item.createdTime = gkeep_item.createdTime
                            alexa_item.updatedTime = gkeep_item.updatedTime
                            alexa_item.resolvedTime = gkeep_item.updatedTime
                            if not alexa_item.isParent:
                                alexa_item.indented = gkeep_item.indented
                                alexa_item.parentId = gkeep_item.parentId
                                alexa_item.parentItemName = gkeep_item.parentItemName
                    else:
                        #using Alexa's timestamp as resolvedTime for sort ording for Gkeep later
                        gkeep_item.resolvedTime = alexa_item.updatedTime
        else:

            all_item_ids = set(alexa_bin.idsOfItems) | set(gkeep_bin.idsOfItems)

            for item_id in all_item_ids:
                alexa_item = alexa_bin.getItem(item_id, is_item_id=True)
                gkeep_item = gkeep_bin.getItem(item_id, is_item_id=True)

                if not alexa_item and gkeep_item:
                    _new_item = ListItem()
                    _new_item.itemName = gkeep_item.itemName
                    _new_item.id = gkeep_item.id
                    _new_item.checked = gkeep_item.checked
                    _new_item.quantity = gkeep_item.quantity
                    _new_item.updatedTime = gkeep_item.updatedTime
                    _new_item.resolvedTime = gkeep_item.updatedTime
                    _new_item.parentItemName = gkeep_item.parentItemName
                    _new_item.indented = gkeep_item.indented
                    if any(i.itemIdentityKey == _new_item.itemIdentityKey for i in alexa_bin.items):
                        logger.warning("[DUPLICATE SKIPPED] '%s' already in Alexa bin (ID: %s) — not copying from GKeep", _new_item.itemName, item_id)
                    else:
                        alexa_bin.add(_new_item)
                elif not gkeep_item and alexa_item:
                    _new_item = ListItem()
                    _new_item.itemName = alexa_item.itemName
                    _new_item.id = alexa_item.id
                    _new_item.checked = alexa_item.checked
                    _new_item.quantity = alexa_item.quantity
                    _new_item.updatedTime = alexa_item.updatedTime
                    _new_item.resolvedTime = alexa_item.updatedTime
                    _new_item.parentItemName = alexa_item.parentItemName
                    _new_item.indented = alexa_item.indented
                    if any(i.itemIdentityKey == _new_item.itemIdentityKey for i in gkeep_bin.items):
                        logger.warning("[DUPLICATE SKIPPED] '%s' already in GKeep bin (ID: %s) — not copying from Alexa", _new_item.itemName, item_id)
                    else:
                        gkeep_bin.add(_new_item)
                elif alexa_item and gkeep_item:
                    if not alexa_item == gkeep_item:
                        a_t = alexa_item.updatedTime
                        g_t = gkeep_item.updatedTime
                        a_checked = alexa_item.checked
                        g_checked = gkeep_item.checked
                        key = alexa_item.itemIdentityKey or gkeep_item.itemIdentityKey
                        if alexa_item > gkeep_item:
                            winner = "ALEXA"
                            reason = f"Alexa newer ({a_t} > {g_t})"
                            gkeep_item.itemName = alexa_item.itemName
                            gkeep_item.checked = alexa_item.checked
                            gkeep_item.quantity = alexa_item.quantity
                            gkeep_item.updatedTime = alexa_item.updatedTime
                            gkeep_item.resolvedTime = alexa_item.updatedTime
                            if not gkeep_item.isParent:
                                gkeep_item.indented = alexa_item.indented
                                gkeep_item.parentItemName = alexa_item.parentItemName
                        else:
                            winner = "GKEEP"
                            reason = f"GKeep newer ({g_t} > {a_t})" if g_t != a_t else f"Equal timestamps — GKeep wins by default ({g_t})"
                            alexa_item.itemName = gkeep_item.itemName
                            alexa_item.checked = gkeep_item.checked
                            alexa_item.quantity = gkeep_item.quantity
                            alexa_item.updatedTime = gkeep_item.updatedTime
                            alexa_item.resolvedTime = gkeep_item.updatedTime
                            if not alexa_item.isParent:
                                alexa_item.indented = gkeep_item.indented
                                alexa_item.parentItemName = gkeep_item.parentItemName
                        logger.info(
                            "[CONFLICT] '%s' | id=%s | key=%s\n"
                            "  Alexa: checked=%-5s  t=%s\n"
                            "  GKeep: checked=%-5s  t=%s\n"
                            "  Winner: %s | %s",
                            alexa_item.itemName, item_id, key,
                            a_checked, a_t,
                            g_checked, g_t,
                            winner, reason,
                        )
                    else:
                        continue
        alexa_bin.propagateParentChecks()
        gkeep_bin.propagateParentChecks()
        alexa_bin.resolveParentIds()
        gkeep_bin.resolveParentIds()


if __name__ == "__main__":
    _acquire_lock()
    import logging as _logging
    from logger_setup import setupLogging
    setupLogging(
        log_to_console=_s.get("log_to_console", True),
        log_to_file=_s.get("log_to_file", True),
        console_level=getattr(_logging, _s.get("log_level", "INFO").upper(), _logging.INFO),
        file_level=getattr(_logging, _s.get("log_file_level", "DEBUG").upper(), _logging.DEBUG),
        max_bytes=_s.get("log_max_bytes", 5 * 1024 * 1024),
        backup_count=_s.get("log_backup_count", 5),
    )
    obj = UpdateLists()
    try:
        obj.updatingLists(max_count=MAX_ITERATIONS or float("inf"))
    except KeyboardInterrupt:
        logger.info("Stopped by user")
