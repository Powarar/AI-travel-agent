import os
from functools import lru_cache

from .executor import FixtureRunExecutor, RunExecutor
from .store import TripStore


def get_store() -> TripStore:
    return TripStore()


@lru_cache(maxsize=1)
def _agent() -> RunExecutor:
    # Отложенный импорт: агент тянет каталог и слоты, а заглушке и тестам это не нужно.
    from .agent_executor import agent_executor_from_env

    return agent_executor_from_env()


def get_executor() -> RunExecutor:
    """Агент по умолчанию; `BACKEND_EXECUTOR=fixture` — заглушка без каталога и LLM."""
    if os.environ.get("BACKEND_EXECUTOR", "agent").strip().lower() == "fixture":
        return FixtureRunExecutor()
    return _agent()
