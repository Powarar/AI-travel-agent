from enum import Enum


class FieldStatus(str, Enum):
    KNOWN = "known"
    INFERRED = "inferred"
    DEFAULTED = "defaulted"
    UNKNOWN = "unknown"


class ConstraintKind(str, Enum):
    HARD = "hard"
    SOFT = "soft"


class DirectionMode(str, Enum):
    EXPLICIT = "explicit"
    OPEN = "open"
    FORBIDDEN = "forbidden"


class BudgetScope(str, Enum):
    TOTAL = "total"
    PER_PERSON = "per_person"


class PriceKind(str, Enum):
    TRANSPORT = "transport"
    LODGING = "lodging"
    TRANSFER = "transfer"
    ACTIVITY = "activity"
    FEE = "fee"


class CostScope(str, Enum):
    TOTAL = "total"
    PER_PERSON = "per_person"
    PER_NIGHT = "per_night"


class BudgetInclude(str, Enum):
    TRANSPORT = "transport"
    LODGING = "lodging"
    ACTIVITIES = "activities"
    TRANSFER = "transfer"


class TransportMode(str, Enum):
    PLANE = "plane"
    TRAIN = "train"
    AUTO = "auto"


class Theme(str, Enum):
    BEACH = "beach"
    CITY = "city"
    NATURE = "nature"
    CULTURE = "culture"
    FOOD = "food"


class Severity(str, Enum):
    BLOCKING = "blocking"
    WARNING = "warning"
    SUGGESTION = "suggestion"


class DataStatus(str, Enum):
    """Честная маркировка данных; `real` — подтверждено внешним источником (seed-схема)."""

    VERIFIED = "verified"
    PARTIALLY_VERIFIED = "partially_verified"
    STALE = "stale"
    UNKNOWN = "unknown"
    SYNTHETIC = "synthetic"
    REAL = "real"


class Stage(str, Enum):
    PARSE = "PARSE"
    CLARIFY = "CLARIFY"
    INSPIRE = "INSPIRE"
    DECIDE = "DECIDE"
    SEARCH = "SEARCH"
    COMPOSE = "COMPOSE"
    VALIDATE = "VALIDATE"
    REPAIR = "REPAIR"
    SHORTLIST = "SHORTLIST"
    COMPARE = "COMPARE"
    EXPLAIN = "EXPLAIN"
    PLAN = "PLAN"
    DIFF = "DIFF"
    CHECKOUT = "CHECKOUT"


class RunStatus(str, Enum):
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"


class Outcome(str, Enum):
    PLAN_READY = "plan_ready"
    AWAITING_ANSWER = "awaiting_answer"
    BEST_EFFORT = "best_effort"
    INFEASIBLE = "infeasible"
    DEGRADED = "degraded"
    UNSUPPORTED = "unsupported"


class EscalationStep(int, Enum):
    RE_RANK = 0
    RECOMPOSE = 1
    RE_SEARCH = 2


class ViolationCode(str, Enum):
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    HARD_CONSTRAINT_UNSATISFIED = "HARD_CONSTRAINT_UNSATISFIED"
    ARRIVAL_AFTER_CHECKIN_DEADLINE = "ARRIVAL_AFTER_CHECKIN_DEADLINE"
    CHECKOUT_BEFORE_DEPARTURE = "CHECKOUT_BEFORE_DEPARTURE"
    TRANSFER_GAP_TOO_SHORT = "TRANSFER_GAP_TOO_SHORT"
    OCCUPANCY_EXCEEDED = "OCCUPANCY_EXCEEDED"
    PET_NOT_ALLOWED = "PET_NOT_ALLOWED"
    CANDIDATES_EMPTY = "CANDIDATES_EMPTY"
    UNKNOWN_AVAILABILITY = "UNKNOWN_AVAILABILITY"
    STALE_SOURCE = "STALE_SOURCE"
    CONFLICTING_SOURCES = "CONFLICTING_SOURCES"
    CONFLICTING_STATE = "CONFLICTING_STATE"
    LEGAL_UNKNOWN = "LEGAL_UNKNOWN"
    FABRICATED_FIELD = "FABRICATED_FIELD"
    UNKNOWN_CURRENCY = "UNKNOWN_CURRENCY"
    DOUBLE_COUNTED = "DOUBLE_COUNTED"
