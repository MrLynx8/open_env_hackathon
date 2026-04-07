from tasks.task_easy import OOMCrashTask
from tasks.task_hard import CascadingFailureTask
from tasks.task_medium import DBPoolTask

TASK_REGISTRY = {
    "oom_crash": OOMCrashTask,
    "db_pool_exhaustion": DBPoolTask,
    "cascading_failure": CascadingFailureTask,
}
