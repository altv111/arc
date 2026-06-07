from __future__ import annotations

from typing import ClassVar

from arc.nodes.base import Node


class GateNode(Node):
    node_type: ClassVar[str] = "gate"


class EvaluateNode(Node):
    node_type: ClassVar[str] = "evaluate"
