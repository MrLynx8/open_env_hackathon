from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from environment import IRTEnvironment
from models import Action, Observation
from tasks import TASK_REGISTRY

app = FastAPI(
    title="Incident Response Triage",
    description="OpenEnv environment for SRE incident diagnosis and remediation",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

env = IRTEnvironment()


@app.post("/reset", response_model=Observation)
def reset(task_id: str = Query(default="oom_crash")):
    if task_id not in TASK_REGISTRY:
        valid = list(TASK_REGISTRY.keys())
        raise HTTPException(status_code=400, detail=f"Unknown task_id. Valid: {valid}")
    return env.reset(task_id)


@app.post("/step")
def step(action: Action):
    try:
        obs, reward, done, info = env.step(action)
        return {
            "observation": obs,
            "reward": reward,
            "done": done,
            "info": info,
        }
    except RuntimeError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err


@app.get("/state")
def state():
    return env.state()


@app.get("/tasks")
def list_tasks():
    return list(TASK_REGISTRY.keys())


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    return {
        "name": "Incident Response Triage",
        "version": "1.0.0",
        "endpoints": ["/reset", "/step", "/state", "/tasks", "/health"],
    }
