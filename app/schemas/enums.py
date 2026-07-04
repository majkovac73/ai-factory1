from enum import Enum


class TaskStatus(str, Enum):
    NEW = "NEW"
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    QA = "QA"
    DONE = "DONE"
    FAILED = "FAILED"


# Defines which status transitions are legal.
# Key = current status, Value = set of statuses it may move to.
TASK_STATUS_TRANSITIONS = {
    TaskStatus.NEW.value: {TaskStatus.PLANNED.value, TaskStatus.FAILED.value},
    TaskStatus.PLANNED.value: {TaskStatus.RUNNING.value, TaskStatus.FAILED.value},
    TaskStatus.RUNNING.value: {TaskStatus.QA.value, TaskStatus.FAILED.value},
    TaskStatus.QA.value: {TaskStatus.DONE.value, TaskStatus.RUNNING.value, TaskStatus.FAILED.value},
    TaskStatus.DONE.value: set(),
    TaskStatus.FAILED.value: {TaskStatus.NEW.value},
}