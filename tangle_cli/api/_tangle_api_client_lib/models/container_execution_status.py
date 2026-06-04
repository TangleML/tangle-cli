from enum import Enum


class ContainerExecutionStatus(str, Enum):
    CANCELLED = "CANCELLED"
    CANCELLING = "CANCELLING"
    FAILED = "FAILED"
    INVALID = "INVALID"
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SKIPPED = "SKIPPED"
    SUCCEEDED = "SUCCEEDED"
    SYSTEM_ERROR = "SYSTEM_ERROR"
    UNINITIALIZED = "UNINITIALIZED"
    WAITING_FOR_UPSTREAM = "WAITING_FOR_UPSTREAM"

    def __str__(self) -> str:
        return str(self.value)
