import datetime
import gkeepapi
import json, logging
from pathlib import Path
from requests.exceptions import ConnectionError
from json.decoder import JSONDecodeError

from list_structures import List, ListItem

logger = logging.getLogger(__name__)

class GoogleKeepLists:
    """Bridges gkeepapi and list_structures by mapping Google Keep checklist notes to local List/ListItem objects."""

    _sort_labels = {"az": "A → Z", "za": "Z → A", "oldest": "oldest → newest", "newest": "newest → oldest"}

    def __init__(self, pinned_only: bool = False, sort_map: "dict[str, str] | None" = None, normalize_quantity_text: bool = True) -> None:
        self.searched_keep_notes = {}
        self.credential_path = Path(__file__).parent / "config" / "runtime_credentials.json"
        self.lists_and_items = {}
        self.lost_children = []
        self.children_to_update = []
        self.pinned_only = pinned_only
        self.sort_map = sort_map or {}
        self._normalize_quantity_text = normalize_quantity_text
        if self.sort_map:
            for list_name, order in self.sort_map.items():
                logger.info("GKeep sort enabled: %s → %s", list_name, self._sort_labels.get(order, order))
        self.gKeepLogin()

    def gKeepLogin(self) -> None:
        """Authenticates using a master token (not a password) from runtime_credentials.json."""
        with open(self.credential_path, "r") as f:
            auth = json.load(f)
        self.config = auth["google"]
        self.keep = gkeepapi.Keep()
        self.keep.authenticate(self.config["username"], self.config["master_token"])

    def getCurrentListsItems(self, resync: bool = False) -> None:
        """Pulls fresh state from GKeep; defers indented children until their parent has been added to the list.

        Args:
            resync: If True, forces a full GKeep resync discarding locally cached state.
        """
        try:
            self.keep.sync(resync=resync)
        except gkeepapi.exception.APIException as e:
            raise ConnectionError(f"GKeep sync error {e.code}") from e
        self.gKeepSearch()
        for name_of_list, g_note in self.searched_keep_notes.items():
            if name_of_list not in self.lists_and_items.keys():
                new_list = List(name_of_list)
                new_list.type = "gkeep"
                new_list.updatedTime = g_note.timestamps.updated
                new_list.createdTime = g_note.timestamps.created
                new_list.internalId = g_note.id
                self.lists_and_items[name_of_list] = new_list
            self.lists_and_items[name_of_list].updatedTime = g_note.timestamps.updated
            seen_internal_ids = {_item.id for _item in g_note.items}
            for stale in [i for i in self.lists_and_items[name_of_list].items if i.internalId not in seen_internal_ids]:
                self.lists_and_items[name_of_list].remove(stale)
            deferred_children = []
            for _item in g_note.items:
                if _item.id in self.lists_and_items[name_of_list].internalIdsOfItems:
                    _list_item = self.lists_and_items[name_of_list].getItem(_item.id, is_internal_id=True)
                    self.compareItemContent(_item, _list_item)
                else:
                    if _item.indented:
                        deferred_children.append(_item)
                        continue
                    new_item = ListItem()
                    new_item.itemName, new_item.quantity = ListItem.parse_quantity(_item.text)
                    new_item.indented = _item.indented
                    new_item.internalId = _item.id
                    new_item.checked = _item.checked
                    new_item.createdTime = self._ts(_item.timestamps.created, g_note.timestamps.created)
                    new_item.updatedTime = self._ts(_item.timestamps.updated, g_note.timestamps.updated)
                    new_item.resolvedTime = new_item.updatedTime
                    new_item.version = _item.version
                    new_item.isParent = bool(_item.subitems)
                    self.lists_and_items[name_of_list].add(new_item)
            for _item in deferred_children:
                if not _item.text.strip():
                    continue
                new_item = ListItem()
                parent_name, _ = ListItem.parse_quantity(_item.parent_item.text)
                new_item.parentItemName = parent_name
                self.findParent(new_item, _item, name_of_list)
                new_item.itemName, new_item.quantity = ListItem.parse_quantity(_item.text)
                new_item.indented = _item.indented
                new_item.internalId = _item.id
                new_item.checked = _item.checked
                new_item.createdTime = self._ts(_item.timestamps.created, g_note.timestamps.created)
                new_item.updatedTime = self._ts(_item.timestamps.updated, g_note.timestamps.updated)
                new_item.resolvedTime = new_item.updatedTime
                new_item.version = _item.version
                new_item.isParent = bool(_item.subitems)
                self.lists_and_items[name_of_list].add(new_item)

    def compareItemContent(self, gnote_item: "gkeepapi.node.ListItem", list_item: ListItem) -> None:
        """Update list_item in place if its content differs from the GKeep server item.

        Args:
            gnote_item: Authoritative gkeepapi item from the server.
            list_item: Local ListItem to update if stale.
        """
        clean_name, qty = ListItem.parse_quantity(gnote_item.text)
        if not (
            list_item.internalParentId == (gnote_item.parent_item.id if gnote_item.parent_item else None)
            and list_item.itemName.casefold() == clean_name.casefold()
            and list_item.quantity == qty
            and list_item.indented == gnote_item.indented
            and list_item.checked == gnote_item.checked
        ):

            list_item.isParent = bool(gnote_item.subitems)
            if gnote_item.indented:
                list_item.internalParentId = gnote_item.parent_item.id
                name_of_list = gnote_item.parent.title
                self.findParent(list_item, gnote_item, name_of_list=name_of_list)
            else:
                self.clearParent(list_item, gnote_item, gnote_item.parent.title)

            list_item.itemName = clean_name
            list_item.quantity = qty
            list_item.indented = gnote_item.indented
            list_item.checked = gnote_item.checked
            list_item.updatedTime = self._ts(gnote_item.timestamps.updated, gnote_item.parent.timestamps.updated)

    def addListEntry(self, name_of_list: str, item: ListItem) -> None:
        """Add item to the GKeep note via gkeepapi, handling indentation for child items.

        Args:
            name_of_list: Target list name.
            item: ListItem to add; its internalId and timestamps are set after the call.
        """
        gnote = self.searched_keep_notes[name_of_list]
        g_item = gnote.add(item.itemText, item.checked)
        item.updatedTime = gnote.timestamps.updated
        item.internalId = g_item.id
        item.version = g_item.version
        if item.indented and item.parentItemName:
            # Newly added item — gkeepapi hasn't set parent_item yet, so we must
            # locate the parent by name and call indent() explicitly.
            parent_list_item = self.lists_and_items[name_of_list].getItem(item.parentItemName)
            if parent_list_item and parent_list_item.internalId:
                parent_g_list = [gi for gi in gnote.items if gi.id == parent_list_item.internalId]
                if parent_g_list:
                    parent_g_item = parent_g_list[0]
                    parent_g_item.indent(g_item)
                    item.internalParentId = parent_g_item.id
                    item.parentId = parent_list_item.id
                    parent_list_item.isParent = True
                    parent_list_item.addChild(item)
                else:
                    logger.warning("GKeep ADD '%s' — parent '%s' not found in note items, added as top-level", item.itemName, item.parentItemName)
            else:
                logger.warning("GKeep ADD '%s' — parent '%s' not in list yet, added as top-level", item.itemName, item.parentItemName)
        elif item.indented:
            self.findParent(item, g_item, name_of_list)
        self.lists_and_items[name_of_list].add(item)
        logger.info("GKeep ADD   '%s' → %s", str(item), name_of_list)

    @staticmethod
    def _ts(item_ts: "datetime.datetime | None", fallback_ts: "datetime.datetime | None" = None) -> "datetime.datetime | None":
        """Return item_ts unless it's None or epoch (1970), in which case return fallback_ts.

        Args:
            item_ts: Primary timestamp from the gkeepapi item.
            fallback_ts: Fallback, usually the parent note's timestamp.
        Returns:
            item_ts if valid, otherwise fallback_ts.
        """
        if item_ts is None or item_ts.year == 1970:
            return fallback_ts
        return item_ts

    def findParent(self, new_item: ListItem, g_item: "gkeepapi.node.ListItem", name_of_list: str) -> None:
        """Reads g_item.parent_item set by gkeepapi; only valid for items already present in the note, not newly added ones.

        Args:
            new_item: Local ListItem to link to its parent.
            g_item: gkeepapi item whose parent_item attribute provides the parent reference.
            name_of_list: List name used to look up the parent in lists_and_items.
        """
        parent_g_item = g_item.parent_item
        if not parent_g_item:
            return
        _parent = self.lists_and_items[name_of_list].getItem(parent_g_item.id, is_internal_id=True)
        if not _parent:
            return
        new_item.parentId = _parent.id
        new_item.internalParentId = _parent.internalId
        new_item.parentItemName = _parent.itemName
        _parent.addChild(new_item)
        parent_g_item.indent(g_item)

    def clearParent(self, item: ListItem, g_item: "gkeepapi.node.ListItem", name_of_list: str) -> None:
        """Unlinks item from its parent in both local state and gkeepapi, dedenting the GKeep node.

        Args:
            item: Local ListItem to de-parent.
            g_item: The corresponding gkeepapi item to dedent.
            name_of_list: List name used to look up the parent in lists_and_items.
        """
        if item.indented:
            _parent = self.lists_and_items[name_of_list].getItem(item.internalParentId, is_internal_id=True)
            parent_g_item = g_item.parent_item
            item.parentId = None
            item.internalParentId = None
            item.parentItemName = None
            item.indented = False
            if _parent:
                _parent.removeChild(item)
                if not _parent._children:
                    _parent.isParent = False
            if parent_g_item:
                # Only needed when we're driving the de-indent; if GKeep already
                # de-indented the item (compareItemContent path), parent_item is None.
                parent_g_item.dedent(g_item)

    def updateListEntry(self, name_of_list: str, item: ListItem) -> None:
        """Applies item changes to gkeepapi, handling indent↔dedent transitions by comparing incoming indented state against current gkeepapi state.

        Args:
            name_of_list: List containing the item.
            item: ListItem with the desired new state to apply.
        """
        gnote = self.searched_keep_notes[name_of_list]
        g_item = [g__item for g__item in gnote.items if g__item.id == item.internalId][0]

        if item.indented and not g_item.indented:
            if not item.internalParentId:
                parent_item = self.lists_and_items[name_of_list].getItem(item.parentId, is_item_id=True)
                if not parent_item:
                    self.children_to_update.append(item)
                    return
                else:
                    parent_g_item = [g__item for g__item in gnote.items if g__item.id == parent_item.internalId][0]
            else:
                parent_g_item = [g__item for g__item in gnote.items if g__item.id == item.internalParentId][0] if g_item.indented else None
            item.internalParentId = parent_g_item.id
            self.findParent(item, g_item, name_of_list)
         
        elif not item.indented and g_item.indented:
            self.clearParent(item, g_item, name_of_list)

        g_item.text = item.itemText
        g_item.checked = bool(item.checked)
        self.lists_and_items[name_of_list].update(item)
        checked_status_str = "CHECKED" if item.checked else "UNCHECKED"
        logger.info("GKeep UPDATE '%s' → %s [%s]", str(item), name_of_list, checked_status_str)

    def deleteListEntry(self, name_of_list: str, item: ListItem) -> None:
        """Delete the GKeep item via gkeepapi and remove it from lists_and_items.

        Args:
            name_of_list: List containing the item.
            item: ListItem to delete.
        """
        gnote = self.searched_keep_notes[name_of_list]
        g_item = [g__item for g__item in gnote.items if g__item.id == item.internalId][0]
        g_item.delete()
        self.lists_and_items[name_of_list].remove(item)
        logger.info("GKeep DELETE '%s' ← %s", str(item), name_of_list)

    def syncList(self, incoming_list: List) -> None:
        """Two-phase sync: first reconcile existing items (by internalId), then add new ones with parents before children.

        Args:
            incoming_list: Desired list state after the current sync pass.
        """
        list_name = incoming_list.name
        current_list: List = self.lists_and_items[list_name]
        current_list.id = incoming_list.id

        if not current_list == incoming_list:
            
            all_internal_item_ids = set(current_list.internalIdsOfItems) | set(incoming_list.internalIdsOfItems)

            for internal_item_id in all_internal_item_ids:
                g_item = current_list.getItem(internal_item_id, is_internal_id=True)
                new_item = incoming_list.getItem(internal_item_id, is_internal_id=True)

                if g_item and not new_item:
                    self.deleteListEntry(list_name, g_item)
                elif g_item and new_item:
                    if g_item.id != new_item.id:
                        self.lists_and_items[list_name].remove(g_item)
                        g_item.id = new_item.id
                        self.lists_and_items[list_name].add(g_item)
                    if not g_item == new_item:
                        self.updateListEntry(list_name, new_item)
                    elif new_item.resolvedTime is not None:
                        # Content identical — propagate resolvedTime without touching GKeep
                        g_item.resolvedTime = new_item.resolvedTime
            new_new_items = [item for item in incoming_list.items if not item.internalId ]
            for _item in new_new_items:
                if _item.parentItemName and not _item.internalParentId:
                    parent_in_new = _item.parentItemName in (p.itemName for p in new_new_items)
                    parent_exists = self.lists_and_items[list_name].getItem(_item.parentItemName) is not None
                    if parent_in_new or parent_exists:
                        self.lost_children.append(_item)
                    else:
                        new_parent_item = ListItem()
                        new_parent_item.itemName = _item.parentItemName
                        self.addListEntry(list_name, new_parent_item)
                        self.lost_children.append(_item)
                else:
                    self.addListEntry(list_name, _item)
            for iitem in self.lost_children:
                self.addListEntry(list_name, iitem)
            self.lost_children.clear()

            for citem in self.children_to_update:
                self.updateListEntry(list_name, citem)
            self.children_to_update.clear()
        if self._normalize_quantity_text:
            self._correct_quantity_text(list_name)
        self._sort_note(list_name)
        self.gKeepSync()
        
    def _correct_quantity_text(self, list_name: str) -> None:
        """Rewrite any GKeep item whose raw text doesn't match the normalized itemText (e.g. 'gum x10003' → 'gum x999')."""
        gnote = self.searched_keep_notes.get(list_name)
        if not gnote:
            return
        for item in self.lists_and_items[list_name].items:
            if not item.internalId:
                continue
            gnote_item = next((i for i in gnote.items if i.id == item.internalId), None)
            if gnote_item and gnote_item.text != item.itemText:
                logger.info("GKeep text correction '%s' → '%s' in %s", gnote_item.text, item.itemText, list_name)
                gnote_item.text = item.itemText

    def _sort_note(self, list_name: str) -> None:
        """Apply the configured sort order to the GKeep note for list_name and sync, if any."""
        if not self.sort_map:
            return
        order = self.sort_map.get(list_name, "none")
        gnote: gkeepapi.node.List = self.searched_keep_notes[list_name]
        match order:
            case "az":
                gnote.sort_items(key=lambda item: item.text.casefold())
            case "za":
                gnote.sort_items(key=lambda item: item.text.casefold(), reverse=True)
            case "newest" | "oldest":
                # use local ListItem timestamps instead of gkeepapi's timestamps.
                # The gkeepapi list method sort_items() internally updates the timestamp on "touch", 
                # thus changing them all to an epoch/timestamp of datetime.datetime.now()
                _epoch = datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)
                ts_map = {
                    li.internalId: (li.resolvedTime or _epoch)
                    for li in self.lists_and_items[list_name].items
                }
                gnote.sort_items(
                    key=lambda item: ts_map.get(item.id, _epoch),
                    reverse=(order == "newest"),
                )
            case _:
                return
        logger.debug("Sorting '%s': %s", list_name, self._sort_labels.get(order, order))
        self.gKeepSync()

    def gKeepSearch(self) -> None:
        """Rebuilds note_search_collection; only includes List-type notes (checklists) — plain text notes are skipped."""
        kwargs = {"pinned": True} if self.pinned_only else {}
        kwargs.update({"func": lambda x: x.type == gkeepapi.node.NodeType.List})
        search_list = list(self.keep.find(**kwargs))
        self.searched_keep_notes = {}
        for note in search_list:
            self.searched_keep_notes[note.title] = note

    def gKeepSync(self) -> None:
        """Push pending gkeepapi changes to Google Keep, re-authenticating on network or JSON errors."""
        try:
            self.keep.sync()
        except gkeepapi.exception.APIException as e:
            raise ConnectionError(f"GKeep sync error {e.code}") from e
        except (ConnectionError, JSONDecodeError) as err:
            logger.warning("gKeepSync error — re-authenticating: %s", err)
            state = self.keep.dump()
            self.keep = gkeepapi.Keep()
            self.keep.authenticate(self.config["username"], self.config["master_token"], state=state)
            self.keep.sync()

    def clearDoneCompleted(self, name_of_list: str) -> None:
        """Delete all checked items from the given list on GKeep and sync.

        Args:
            name_of_list: Name of the list to clear.
        """
        for shopping_item in self.lists_and_items[name_of_list].items:
            if shopping_item.checked:
                self.deleteListEntry(name_of_list, shopping_item)
        self.gKeepSync()


if __name__ == "__main__":
    obj = GoogleKeepLists()
    x = obj.getCurrentListsItems()
    # obj.gKeepBackup("Groceries/Shopping List")
    obj.clearDoneCompleted("Groceries/Shopping List")
