import logging
from pathlib import Path

import pyalexalist

from list_structures import List, ListItem

logger = logging.getLogger(__name__)

class AlexaLists:
    """Bridges pyalexalist and list_structures — maps Alexa server state to local List/ListItem objects."""

    def __init__(self, cookie_expiry_retries: int = 0, retry_interval_seconds: int = 30, amazon_domain: str = "amazon.com", service_auth_path: "Path | None" = None) -> None:
        self.pyalexalist = pyalexalist.AlexaList(cookie_expiry_retries=cookie_expiry_retries, retry_interval_seconds=retry_interval_seconds, amazon_domain=amazon_domain, service_auth_path=service_auth_path)
        self.lists_and_items = {}
        self.searched_alexa_lists = {}

    def getCurrentListsItems(self) -> None:
        """Fetch current Alexa lists server state and merge into lists_and_items, deferring bracket-prefixed children."""
        deferred_orphaned_items_list = []
        self.pyalexalist.pull()
        self.alexaListSearch()
        
        for name_of_list, alexa_lst in self.searched_alexa_lists.items():
            if name_of_list not in self.lists_and_items:
                new_list = List(name_of_list)
                new_list.internalId = alexa_lst.id
                new_list.type = "alexa"
                self.lists_and_items[name_of_list] = new_list

            seen_internal_ids = {alexa_item.id for alexa_item in alexa_lst.items}
            for stale in [i for i in self.lists_and_items[name_of_list].items if i.internalId not in seen_internal_ids]:
                self.lists_and_items[name_of_list].remove(stale)

            for _alexa_item in alexa_lst.items:
                if _alexa_item.id in self.lists_and_items[name_of_list].internalIdsOfItems:
                    _list_item = self.lists_and_items[name_of_list].getItem(_alexa_item.id, is_internal_id=True)
                    self.compareItemContent(_alexa_item, _list_item, name_of_list)
                else:
                    new_item = ListItem()
                    _parent_name, _item_name = List.parentExists(_alexa_item.itemName)
                    if _parent_name:
                        deferred_orphaned_items_list.append((_alexa_item, name_of_list))
                        continue
                    self.lists_and_items[name_of_list].findParent(new_item, _alexa_item.itemName)
                    new_item.internalId = _alexa_item.id
                    new_item.checked = _alexa_item.checked
                    new_item.quantity = _alexa_item.quantity
                    new_item.createdTime = _alexa_item.createdTime
                    new_item.updatedTime = _alexa_item.updatedTime
                    new_item.version = _alexa_item.version
                    self.lists_and_items[name_of_list].add(new_item)

        for _aitem, name_of_list in deferred_orphaned_items_list:
            new_item = ListItem()
            self.lists_and_items[name_of_list].findParent(new_item, _aitem.itemName)
            new_item.internalId = _aitem.id
            new_item.checked = _aitem.checked
            new_item.quantity = _aitem.quantity
            new_item.createdTime = _aitem.createdTime
            new_item.updatedTime = _aitem.updatedTime
            new_item.version = _aitem.version
            self.lists_and_items[name_of_list].add(new_item)

    def compareItemContent(self, alexa_item: "pyalexalist.ListItem", list_item: ListItem, name_of_list: str) -> None:
        """Update list_item in place if its content differs from the Alexa server item.

        Args:
            alexa_item: Authoritative pyalexalist item from the server.
            list_item: Local ListItem to update if stale.
            name_of_list: List name used to re-run findParent if the item name changed.
        """
        if not (
            list_item.itemName.casefold() == alexa_item.itemName.strip().casefold()
            and list_item.checked == alexa_item.checked
            and list_item.quantity == alexa_item.quantity
            and list_item.version == alexa_item.version
        ):
            self.lists_and_items[name_of_list].findParent(list_item, alexa_item.itemName)
            list_item.checked = alexa_item.checked
            list_item.quantity = alexa_item.quantity
            list_item.version = alexa_item.version
            list_item.updatedTime = alexa_item.updatedTime

    def addListEntry(self, name_of_list: str, item: ListItem) -> None:
        """Add item to Alexa via the API, then register it in lists_and_items.

        Args:
            name_of_list: Target list name.
            item: ListItem to create; its internalId and timestamps are set after the API call.
        """
        if not item.itemName.strip():
            logger.warning("Alexa ADD skipped — empty item name in %s", name_of_list)
            return
        alist = self.searched_alexa_lists[name_of_list]
        if self.lists_and_items[name_of_list].supportsQuantity:
            alexa_item = alist.add(item.qualifiedItemName, quantity=item.quantity)
        else:
            alexa_item = alist.add(item.renderedItemText)
        item.internalId = alexa_item.id
        item.createdTime = alexa_item.createdTime
        item.updatedTime = alexa_item.updatedTime
        item.version = alexa_item.version
        self.lists_and_items[name_of_list].add(item)
        logger.info("Alexa ADD   '%s' → %s", str(item), name_of_list)

    def updateListEntry(self, name_of_list: str, item: ListItem) -> None:
        """Apply changes from item to the matching Alexa entry, updating name/quantity/checked as needed.

        Args:
            name_of_list: List containing the item.
            item: ListItem with the desired new state.
        """
        txt_list = []
        alexa_item = self.pyalexalist.get(name_of_list).get(id=item.internalId)
        _current_item = self.lists_and_items[name_of_list].getItem(item.internalId, is_internal_id=True)
        if _current_item is None:
            logger.warning("Alexa UPDATE skipped — item '%s' not found in local state for %s", str(item), name_of_list)
            return
        if self.lists_and_items[name_of_list].supportsQuantity:
            if item.qualifiedItemName != _current_item.qualifiedItemName:
                updating_name_str = f"Updating '{_current_item}' to '{item}' in `{name_of_list}` LIST"
                txt_list.append(updating_name_str)
                alexa_item.itemName = item.qualifiedItemName

            if item.quantity != _current_item.quantity:
                if item.quantity is None:
                    txt_list.append(f"Clearing quantity from '{str(item)}' in `{name_of_list}` LIST")
                    alexa_item.quantity = None
                else:
                    txt_list.append(f"Updating '{str(item)}' in `{name_of_list}` LIST quantity to {item.quantity}")
                    alexa_item.quantity = item.quantity
        else:
            if item.renderedItemText != _current_item.renderedItemText:
                updating_name_str = f"Updating '{_current_item}' to '{item}' in `{name_of_list}` LIST"
                txt_list.append(updating_name_str)
                alexa_item.itemName = item.renderedItemText

        if item.checked != _current_item.checked:
            checked_status_str = "CHECKED" if item.checked else "UNCHECKED"
            updating_status_str = f"Updating '{str(item)}' in `{name_of_list}` LIST status to {checked_status_str}"
            txt_list.append(updating_status_str)
            alexa_item.checked = item.checked
        for txt in txt_list:
            logger.info("Alexa UPDATE %s", txt)

    def updateOrphanedListEntry(self, name_of_list: str, item: ListItem) -> None:
        """Renames an item whose parent no longer exists; strips the bracket prefix so it becomes a plain top-level item on Alexa.

        Args:
            name_of_list: List containing the orphaned item.
            item: The orphaned child item to promote to top-level.
        """
        alexa_item = self.pyalexalist.get(name_of_list).get(id=item.internalId)
        alexa_item.itemName = item.qualifiedItemName
        self.lists_and_items[name_of_list].update(item)
        logger.warning("Alexa UPDATE orphan '%s' in %s — parent not found, cleared parent ref", str(item), name_of_list)

    def deleteListEntry(self, name_of_list: str, item: ListItem) -> None:
        """Mark the Alexa item for deletion and remove it from lists_and_items.

        Args:
            name_of_list: List containing the item.
            item: ListItem to delete.
        """
        alexa_lst = self.pyalexalist.get(name_of_list)
        alexa_item = alexa_lst.get(id=item.internalId)
        alexa_item.delete()
        self.lists_and_items[name_of_list].remove(item)
        logger.info("Alexa DELETE '%s' ← %s", str(item), name_of_list)

    def syncList(self, incoming_list: List) -> None:
        """Reconcile incoming_list against current local state, then push to Alexa.

        Args:
            incoming_list: Desired list state after the current sync pass.
        """

        list_name = incoming_list.name
        current_list: List = self.lists_and_items[incoming_list.name]
        current_list.id = incoming_list.id

        if not incoming_list == current_list:
            all_internal_item_ids = set(current_list.internalIdsOfItems) | set(incoming_list.internalIdsOfItems)

            for internal_item_id in all_internal_item_ids:
                old_item = current_list.getItem(internal_item_id, is_internal_id=True)
                new_item = incoming_list.getItem(internal_item_id, is_internal_id=True)

                if old_item and not new_item:
                    self.deleteListEntry(list_name, old_item)
                elif new_item and old_item:
                    if old_item.id != new_item.id:
                        self.lists_and_items[list_name].remove(old_item)
                        old_item.id = new_item.id
                        self.lists_and_items[list_name].add(old_item)
                    if not new_item == old_item:
                        self.updateListEntry(incoming_list.name, new_item)
            new_new_items = [item for item in incoming_list.items if not item.internalId]
            for _item in new_new_items:
                self.addListEntry(list_name, _item)
        self.pyalexalist.push()

    def clearDoneCompleted(self, name_of_list: str) -> None:
        """Delete all checked items from the given list on Alexa and push the deletions.

        Args:
            name_of_list: Name of the list to clear.
        """
        for shopping_item in self.lists_and_items[name_of_list].items:
            if shopping_item.checked:
                self.deleteListEntry(name_of_list, shopping_item)
        self.pyalexalist.push()


    def alexaListSearch(self) -> None:
        """Rebuild searched_alexa_lists from a fresh pyalexalist.all() fetch."""
        #TODO exclude archives?
        search_list = self.pyalexalist.all()
        if not search_list:
            raise ConnectionError("sync returned no lists — skipping iteration")
        self.searched_alexa_lists = {}
        for _alist in search_list:
            self.searched_alexa_lists[_alist.name] = _alist

if __name__ == "__main__":
    obj = AlexaLists()
    obj.getCurrentListsItems()
    obj.clearDoneCompleted("SHOP")
