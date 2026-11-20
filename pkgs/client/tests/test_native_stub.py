import ast
from pathlib import Path

import tcord_core
import tcord_core._native as native

STUB = Path(tcord_core.__file__).with_name("_native.pyi")


def _stub_classes() -> dict[str, set[str]]:
    tree = ast.parse(STUB.read_text())
    classes: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            members = set()
            for item in node.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                    members.add(item.name)
                elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    members.add(item.target.id)
            classes[node.name] = members - {"__init__"}
    return classes


def test_stub_declares_every_native_class() -> None:
    public = {name for name in dir(native) if not name.startswith("_")}
    assert set(_stub_classes()) == public


def test_stub_members_exist_on_native_classes() -> None:
    for name, members in _stub_classes().items():
        cls = getattr(native, name)
        missing = {member for member in members if not hasattr(cls, member)}
        assert not missing, f"{name} lacks {missing}"
