"""Read-only master data: branch hierarchy and the service menu tree.

Loaded from JSON for the demo. To source this from Queue Pro instead, implement the same
public methods against its API/DB and swap the instance created in ``app.main``.
"""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


class Branch(BaseModel):
    code: str
    name: str
    address: str = ""
    district_id: str
    division_id: str


class District(BaseModel):
    id: str
    name: str
    division_id: str


class Division(BaseModel):
    id: str
    name: str


class ServiceNode(BaseModel):
    id: str
    type: Literal["page", "service"]
    label: str
    code: str | None = None  # required for type == "service"
    parent_id: str | None = None
    children: list["ServiceNode"] = []


class MasterDataRepository:
    def __init__(self, data_dir: Path):
        self._divisions: dict[str, Division] = {}
        self._districts: dict[str, District] = {}
        self._branches: dict[str, Branch] = {}
        self._service_roots: list[ServiceNode] = []
        self._service_nodes: dict[str, ServiceNode] = {}
        self._load_branches(data_dir / "branches.json")
        self._load_services(data_dir / "services.json")

    # --- loading ---

    def _load_branches(self, path: Path) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        for div in raw["divisions"]:
            division = Division(id=div["id"], name=div["name"])
            for dist in div["districts"]:
                district = District(id=dist["id"], name=dist["name"], division_id=division.id)
                for br in dist["branches"]:
                    branch = Branch(**br, district_id=district.id, division_id=division.id)
                    self._branches[branch.code] = branch
                # Only expose districts/divisions that actually have branches.
                if dist["branches"]:
                    self._districts[district.id] = district
                    self._divisions[division.id] = division

    def _load_services(self, path: Path) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))

        def build(item: dict, parent_id: str | None) -> ServiceNode:
            node = ServiceNode(
                id=item["id"], type=item["type"], label=item["label"],
                code=item.get("code"), parent_id=parent_id,
            )
            if node.type == "service" and not node.code:
                raise ValueError(f"Service node '{node.id}' has no code")
            if node.id in self._service_nodes:
                raise ValueError(f"Duplicate service node id '{node.id}'")
            self._service_nodes[node.id] = node
            node.children = [build(child, node.id) for child in item.get("children", [])]
            return node

        self._service_roots = [build(item, None) for item in raw["services"]]

    # --- branches ---

    def divisions(self) -> list[Division]:
        return sorted(self._divisions.values(), key=lambda d: d.name)

    def districts(self, division_id: str) -> list[District]:
        return sorted(
            (d for d in self._districts.values() if d.division_id == division_id), key=lambda d: d.name
        )

    def branches(self, district_id: str) -> list[Branch]:
        return sorted(
            (b for b in self._branches.values() if b.district_id == district_id), key=lambda b: b.name
        )

    def division(self, division_id: str) -> Division | None:
        return self._divisions.get(division_id)

    def district(self, district_id: str) -> District | None:
        return self._districts.get(district_id)

    def branch(self, code: str) -> Branch | None:
        return self._branches.get(code)

    def search_branches(
        self, query: str, *, division_id: str | None = None, district_id: str | None = None
    ) -> list[Branch]:
        q = query.strip().lower()
        if len(q) < 3:
            return []
        hits = [
            b for b in self._branches.values()
            if (district_id is None or b.district_id == district_id)
            and (division_id is None or b.division_id == division_id)
            and (q in b.name.lower() or q in b.address.lower())
        ]
        # Names starting with the query first, then alphabetical.
        return sorted(hits, key=lambda b: (not b.name.lower().startswith(q), b.name))

    # --- services ---

    def service_menu(self, page_id: str | None = None) -> list[ServiceNode]:
        """Items on a service menu page; ``None`` is the root page."""
        if page_id is None:
            return self._service_roots
        node = self._service_nodes.get(page_id)
        return node.children if node and node.type == "page" else []

    def service_node(self, node_id: str) -> ServiceNode | None:
        return self._service_nodes.get(node_id)

    def find_service_on_page(self, text: str, page_id: str | None) -> ServiceNode | None:
        q = text.strip().lower()
        items = self.service_menu(page_id)
        for node in items:
            if node.label.lower() == q:
                return node
        matches = [node for node in items if q and q in node.label.lower()]
        return matches[0] if len(matches) == 1 else None
