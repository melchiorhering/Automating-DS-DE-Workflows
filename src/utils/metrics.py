import importlib
from pathlib import Path
from types import ModuleType
from typing import Dict


def load_metric_registry() -> Dict[str, callable]:
    root = Path(__file__).parent.parent / "benchmark" / "evaluators" / "metrics"
    registry: Dict[str, callable] = {}

    for file in root.rglob("*.py"):
        if file.name == "__init__.py":
            continue
        mod_name = ".".join(file.with_suffix("").relative_to(Path(__file__).parent.parent).parts)
        module: ModuleType = importlib.import_module(mod_name)
        for name, obj in vars(module).items():
            if callable(obj) and not name.startswith("_"):
                registry[name] = obj

    registry.setdefault("infeasible", lambda *_a, **_kw: float("nan"))
    return registry
