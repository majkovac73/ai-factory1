from enum import Enum


class TaskStatus(str, Enum):
    NEW = "NEW"
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    QA = "QA"
    DONE = "DONE"
    FAILED = "FAILED"
