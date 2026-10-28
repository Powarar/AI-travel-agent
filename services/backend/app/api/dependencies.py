from .executor import FixtureRunExecutor, RunExecutor
from .store import TripStore


def get_store() -> TripStore:
    return TripStore()


def get_executor() -> RunExecutor:
    return FixtureRunExecutor()
