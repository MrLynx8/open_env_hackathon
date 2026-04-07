from typing import Any, Dict, List, Tuple

from models import Action, Observation, Reward
from tasks import TASK_REGISTRY


class IRTEnvironment:
    def __init__(self, max_steps: int = 10):
        self._task = None
        self._state_data: Dict[str, Any] = {}
        self._step_count = 0
        self._max_steps = max_steps
        self._history: List[Action] = []
        self._task_id = None

    def reset(self, task_id: str = "oom_crash") -> Observation:
        if task_id not in TASK_REGISTRY:
            valid = list(TASK_REGISTRY.keys())
            raise ValueError(f"Unknown task_id '{task_id}'. Valid: {valid}")

        self._task_id = task_id
        self._task = TASK_REGISTRY[task_id]()
        self._step_count = 0
        self._history = []
        self._state_data = self._task.get_initial_state()
        return self._build_obs(done=False)

    def step(self, action: Action) -> Tuple[Observation, Reward, bool, Dict[str, Any]]:
        if self._task is None:
            raise RuntimeError("Call reset() before step()")

        self._step_count += 1
        self._history.append(action)

        reward = self._task.grade(action, self._step_count, self._history[:-1])
        done = reward.score >= 0.85 or self._step_count >= self._max_steps
        obs = self._build_obs(done=done)

        info = {
            "step": self._step_count,
            "task_id": self._task_id,
        }
        return obs, reward, done, info

    def state(self) -> Dict[str, Any]:
        return {
            "task_id": self._task_id,
            "step": self._step_count,
            "max_steps": self._max_steps,
            "history_length": len(self._history),
            "history": [a.model_dump() for a in self._history],
        }

    def _build_obs(self, done: bool) -> Observation:
        s = self._state_data
        hint = None

        # Progressive hint for hard tasks when an episode is dragging.
        if self._step_count >= 5 and not done:
            hint = "Hint: check the service furthest upstream in service_map."

        return Observation(
            incident_id=s["incident_id"],
            task_id=self._task_id,
            alerts=s["alerts"],
            logs=s["logs"],
            metrics=s["metrics"],
            service_map=s["service_map"],
            step=self._step_count,
            max_steps=self._max_steps,
            done=done,
            hint=hint,
        )
