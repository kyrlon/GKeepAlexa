
import random
import datetime
import re

_QTY_SUFFIX_X     = re.compile(r'^(.+?)\s*[×xX*]\s*(\d+)$')   # Potatoes ×4, Potatoes x4, Potatoes * 4
_QTY_PREFIX_X     = re.compile(r'^(\d+)\s*[×xX*]\s*(.+)$')    # 4x Potatoes, 4 * Potatoes
_QTY_SUFFIX_PAREN = re.compile(r'^(.+?)\s*\((\d+)\)$')        # Potatoes (4)
_QTY_PREFIX_PAREN = re.compile(r'^\((\d+)\)\s*(.+)$')         # (4) Potatoes

class Element:
    """Base class providing a shared ID and created/updated timestamps."""

    @classmethod
    def generateId(cls) -> str:
        return "{:04x}-{:04x}-{:04x}-{:012x}".format(
            random.randint(0x0000, 0xFFFF),
            random.randint(0x0000, 0xFFFF),
            random.randint(0x0000, 0xFFFF),
            random.randint(0x000000000000, 0xFFFFFFFFFFFF),
        )

    @classmethod
    def int_to_dt(cls, t: float | int) -> datetime.datetime:
        """Converts a timestamp to UTC datetime, auto-detecting milliseconds by comparing against the current epoch second."""
        if t > datetime.datetime.now(datetime.timezone.utc).timestamp():
            t = t/1000
        return datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc)

    @property
    def createdTime(self) -> "datetime.datetime | None":
        return self._createdTime

    @createdTime.setter
    def createdTime(self, value: "datetime.datetime | int | float"):
        if isinstance(value, (int, float)):
            value = self.int_to_dt(value)
        self._createdTime = value

    @property
    def updatedTime(self) -> "datetime.datetime | None":
        return self._updatedTime

    @updatedTime.setter
    def updatedTime(self, value: "datetime.datetime | int | float"):
        if isinstance(value, (int, float)):
            value = self.int_to_dt(value)
        self._updatedTime = value

    @property
    def id(self) -> str:
        return self._id

    @id.setter
    def id(self, value: str):
        self._id = value

    @property
    def internalId(self) -> "str | None":
        return self._internal_id

    @internalId.setter
    def internalId(self, value: "str | None"):
        self._internal_id = value

class List(Element):
    """Shared list data model used by both AlexaLists and GoogleKeepLists."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.itemsList_d = {}
        self._id = self.generateId()
        self._type = ""
        self._internal_id = None
        self._createdTime = None
        self._updatedTime = None
        self._resolvedTime = None

    def add(self, new_item: "ListItem") -> None:
        """Add a ListItem to this list."""
        self.itemsList_d[new_item.id] = new_item

    def update(self, new_item: "ListItem") -> None:
        """Replace the item in this list matching new_item.id."""
        if new_item.id in self.itemsList_d.keys():
            del self.itemsList_d[new_item.id]
        self.itemsList_d[new_item.id] = new_item

    def remove(self, new_item: "ListItem") -> None:
        """Remove the item matching new_item.id from this list."""
        del self.itemsList_d[new_item.id]

    def getItem(self, key: "str | None", is_item_id: bool = False, is_internal_id: bool = False) -> "ListItem | None":
        if key is None:
            return None
        _item = None
        if is_item_id:
            _item = self.itemsList_d.get(key, None)
        if is_internal_id:
            try:
                found = next(k for k, value in self.itemsList_d.items() if value.internalId == key)
                _item = self.itemsList_d.get(found, None)
            except StopIteration:
                pass
        if not is_item_id and not is_internal_id:
            try:
                found = next(k for k, value in self.itemsList_d.items() if value.itemName == key)
                _item = self.itemsList_d.get(found, None)
            except StopIteration:
                pass

        return _item

    def cascadeParentToChildren(self) -> None:
        """Cascade parent checked state to children, but only when the parent is newer.
        Prevents a stale parent from overwriting a child the user explicitly changed."""
        for item in self.items:
            if not item.parentItemName:
                continue
            parent = self.getItem(item.parentItemName)
            if parent is None:
                continue
            if item.checked != parent.checked:
                parent_t = parent.updatedTime
                child_t = item.updatedTime
                if parent_t is None or child_t is None or parent_t >= child_t:
                    item.checked = parent.checked
                    item.updatedTime = parent.updatedTime

    def propagateParentChecks(self) -> None:
        """Bidirectional parent/child check propagation based on updatedTime:
        parent checked and newer → forward propagate to all children;
        any child unchecked and newer → reverse propagate to uncheck the parent."""
        parent_map = {item.itemName.casefold(): item for item in self.items if not item.parentItemName}

        children_by_parent = {}
        for item in self.items:
            if item.parentItemName:
                key = item.parentItemName.casefold()
                if key in parent_map:
                    children_by_parent.setdefault(key, []).append(item)

        for key, parent in parent_map.items():
            children = children_by_parent.get(key, [])
            if not children:
                continue
            if parent.checked:
                newer_unchecked = [
                    c for c in children
                    if not c.checked
                    and c.updatedTime is not None
                    and parent.updatedTime is not None
                    and c.updatedTime > parent.updatedTime
                ]
                if newer_unchecked:
                    newest = max(newer_unchecked, key=lambda c: c.updatedTime)
                    parent.checked = False
                    parent.updatedTime = newest.updatedTime
                elif parent.updatedTime is not None:
                    for child in children:
                        if not child.checked:
                            child.checked = True
                            child.updatedTime = parent.updatedTime

    def resolveParentIds(self) -> None:
        """Re-resolves parentId from parentItemName after items are copied across lists, since parentId is list-local and meaningless cross-list."""
        for _item in self.items:
            if not _item.isParent:
                _parent = self.getItem(_item.parentItemName)
                _item.parentId = _parent.id if _parent else None

    @staticmethod
    def parentExists(item_name: str) -> "tuple[str | None, str]":
        """Parses '[Parent] Name' bracket encoding used by Alexa; returns (parent, name) or (None, name)."""
        m = re.match(r"\[(.+?)\]\s*(.+)", item_name)
        if m:
            parent, actual_name = m.groups()
            return parent.strip(), actual_name.strip()
        return None, item_name.strip()

    def findParent(self, item: "ListItem", raw_name: str) -> None:
        """Parses bracket encoding from raw_name and links item to its parent in this list.
        Clears stale parent state when raw_name has no bracket prefix."""
        _parent_name, _item_name = List.parentExists(raw_name)
        if _parent_name:
            _parent_item = self.getItem(_parent_name)
            if not _parent_item:
                item.parentItemName = _parent_name
                item.indented = True
            else:
                _parent_item.addChild(item)
                _parent_item.isParent = True
                item.parentId = _parent_item.id
                item.internalParentId = _parent_item.internalId
                item.indented = True
                item.parentItemName = _parent_name
        else:
            item.indented = False
            item.parentItemName = None
            item.parentId = None
            item.internalParentId = None
        item.itemName = _item_name


    @property
    def items(self) -> list["ListItem"]:
        return list(self.itemsList_d.values())

    @property
    def namesOfUncheckedItems(self) -> list[str]:
        return sorted([item.itemName for item in self.itemsList_d.values() if not item.checked])

    @property
    def namesOfCheckedItems(self) -> list[str]:
        return sorted([item.itemName for item in self.itemsList_d.values() if item.checked])

    @property
    def idsOfItems(self) -> list[str]:
        return [item.id for item in self.itemsList_d.values()]

    @property
    def internalIdsOfItems(self) -> "list[str | None]":
        return [item.internalId for item in self.itemsList_d.values()]

    @property
    def type(self) -> str:
        return self._type

    @type.setter
    def type(self, value: str):
        self._type = value

    @property
    def supportsQuantity(self) -> bool:
        """False when the list name contains 'todo' — Alexa to-do lists have no quantity field."""
        return self.name.casefold() != "todo"

    def __gt__(self, other_list: "List") -> bool:
        if self.updatedTime is None and other_list.updatedTime is None:
            return False
        if self.updatedTime is None:
            return False
        if other_list.updatedTime is None:
            return True
        return self.updatedTime > other_list.updatedTime

    def __lt__(self, other_list: "List") -> bool:
        if self.updatedTime is None and other_list.updatedTime is None:
            return False
        if self.updatedTime is None:
            return True
        if other_list.updatedTime is None:
            return False
        return self.updatedTime < other_list.updatedTime

    def __eq__(self, other_list: "List") -> bool:
        if not self.type == other_list.type:
            return False

        all_item_ids = set(self.idsOfItems) | set(other_list.idsOfItems)

        for item_id in all_item_ids:
            item1 = self.getItem(item_id, is_item_id=True)
            item2 = other_list.getItem(item_id, is_item_id=True)

            if not item1 and item2:
                return False
            elif item1 and not item2:
                return False
            elif item1 and item2:
                if not item1 == item2:
                    return False
        return True

class ListItem(Element):
    """Shared item data model used by both AlexaLists and GoogleKeepLists."""

    def __init__(self) -> None:
        self._itemName = ""
        self._id = self.generateId()
        self._internal_id = None
        self._parent_item_name = None
        self._parent_id = None
        self._internal_parent_id = None
        self._is_parent = False
        self._indented = False
        self._checked = False
        self._createdTime = None
        self._updatedTime = None
        self._resolvedTime = None
        self._version = None
        self._children = []
        self._quantity = None

    @property
    def itemName(self) -> str:
        return self._itemName

    @itemName.setter
    def itemName(self, value: str):
        self._itemName = value.strip()

    @property
    def checked(self) -> bool:
        return self._checked

    @checked.setter
    def checked(self, value: bool):
        self._checked = value

    @property
    def version(self) -> "int | None":
        return self._version

    @version.setter
    def version(self, value: "int | None"):
        self._version = value

    @property
    def indented(self) -> bool:
        return self._indented

    @indented.setter
    def indented(self, value: bool):
        self._indented = value

    @property
    def parentItemName(self) -> "str | None":
        if self.isParent:
            return None
        return self._parent_item_name

    @parentItemName.setter
    def parentItemName(self, value: "str | None"):
        if self.isParent and value is not None:
            raise ValueError(f"'{self._itemName}' is a parent item — cannot assign parent '{value}'.")
        self._parent_item_name = value

    @property
    def parentId(self) -> "str | None":
        if self.isParent:
            return None
        return self._parent_id

    @parentId.setter
    def parentId(self, value: "str | None"):
        if self.isParent and value is not None:
            raise ValueError(f"'{self._itemName}' is a parent item — cannot assign parentId '{value}'.")
        self._parent_id = value

    @property
    def internalParentId(self) -> "str | None":
        if self.isParent:
            return None
        return self._internal_parent_id

    @internalParentId.setter
    def internalParentId(self, value: "str | None"):
        if self.isParent and value is not None:
            raise ValueError(f"'{self._itemName}' is a parent item — cannot assign internalParentId '{value}'.")
        self._internal_parent_id = value

    @property
    def isParent(self) -> bool:
        return self._is_parent

    @isParent.setter
    def isParent(self, value: bool):
        self._is_parent = bool(value)

    def addChild(self, child: "ListItem") -> None:
        if self.isParent and not any(c is child for c in self._children):
            self._children.append(child)

    def removeChild(self, child: "ListItem") -> None:
        if self.isParent:
            self._children = [c for c in self._children if c is not child]

    @property
    def children(self) -> list["ListItem"]:
        if not self.isParent:
            raise ValueError("Error: child items cannot reference children!")
        return self._children

    @property
    def itemIdentityKey(self) -> str:
        """Stable normalized key used to match the same logical item across list providers.

        This key identifies the item by its parent/child position and base item name.

        Format:
        - 'parent::child' for child items
        - '::item' for top-level items
        """
        parent = (self.parentItemName or "").strip().casefold()
        name = (self.itemName or "").strip().casefold()
        return f"{parent}::{name}"


    @property
    def quantity(self) -> "int | None":
        return self._quantity

    @quantity.setter
    def quantity(self, value: "int | None"):
        v = int(value) if value is not None else None
        self._quantity = v if v and v > 1 else None

    @staticmethod
    def parse_quantity(text: str) -> "tuple[str, int | None]":
        """Parses quantity from any supported GKeep text format; returns (clean_name, quantity or None)."""
        t = text.strip()
        for pattern, name_grp, qty_grp in (
            (_QTY_SUFFIX_X,     1, 2),
            (_QTY_PREFIX_X,     2, 1),
            (_QTY_SUFFIX_PAREN, 1, 2),
            (_QTY_PREFIX_PAREN, 2, 1),
        ):
            m = pattern.match(t)
            if m:
                return m.group(name_grp).strip(), int(m.group(qty_grp))
        return t, None

    @property
    def itemText(self) -> str:
        """Item text name with xN suffix"""
        if self._quantity and self._quantity > 1:
            return f"{self.itemName} x{self._quantity}"
        return self.itemName

    @property
    def qualifiedItemName(self) -> str:
        """Item name with parent prefix for child items — no quantity suffix."""
        if self.indented:
            return f"[{self.parentItemName}] {self.itemName}"
        return self.itemName

    @property
    def renderedItemText(self) -> str:
        """Final rendered item text — parent prefix and quantity suffix combined."""
        txt = self.qualifiedItemName
        if self._quantity and self._quantity > 1:
            txt = f"{txt} x{self._quantity}"
        return txt
    
    @property
    def resolvedTime(self) -> "datetime.datetime | None":
        """Settled from the best available source for sync conflict resolution; may differ from updatedTime."""
        return self._resolvedTime

    @resolvedTime.setter
    def resolvedTime(self, value: "datetime.datetime | int | float"):
        if isinstance(value, (int, float)):
            value = self.int_to_dt(value)
        self._resolvedTime = value

    def __str__(self) -> str:
        return self.renderedItemText

    def __repr__(self) -> str:
        return (
            f"ListItem(itemName={self.itemName!r}, parentItemName={self.parentItemName!r}, "
            f"quantity={self._quantity!r}, checked={self._checked!r}, id={self._id!r})"
        )

    def __eq__(self, other_item: "ListItem") -> bool:
        """Uses parentItemName instead of parentId so equality holds across lists where parentId is list-local.
        isParent is excluded — it is structural metadata derived from children, not a synced content field."""
        if self.isParent or other_item.isParent:
            return (
                self.itemName.casefold() == other_item.itemName.casefold() and
                self.checked == other_item.checked and
                self.id == other_item.id
            )
        return (
            self.itemName.casefold() == other_item.itemName.casefold() and
            self.checked == other_item.checked and
            self.indented == other_item.indented and
            self.id == other_item.id and
            (self.parentItemName or "").casefold() == (other_item.parentItemName or "").casefold() and
            self.quantity == other_item.quantity
        )

    def __lt__(self, other_item: "ListItem") -> bool:
        if self.updatedTime is None and other_item.updatedTime is None:
            return False
        if self.updatedTime is None:
            return True
        if other_item.updatedTime is None:
            return False
        return self.updatedTime < other_item.updatedTime

    def __gt__(self, other_item: "ListItem") -> bool:
        if self.updatedTime is None and other_item.updatedTime is None:
            return False
        if self.updatedTime is None:
            return False
        if other_item.updatedTime is None:
            return True
        return self.updatedTime > other_item.updatedTime
