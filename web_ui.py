#!/usr/bin/env python3
"""
Web UI for Phone Agent - AI-powered phone automation tasks management.

This module provides a web interface to manage automation tasks,
including creating, editing, deleting, running, and stopping tasks.
"""

import json
import os
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from phone_agent import PhoneAgent
from phone_agent.agent import AgentConfig
from phone_agent.model import ModelConfig

# Task models
class TaskCreate(BaseModel):
    name: str
    description: str
    base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    model: str = "autoglm-phone"
    apikey: str = "EMPTY"
    device_id: Optional[str] = None
    max_steps: int = 100
    lang: str = "cn"

class TaskUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    apikey: Optional[str] = None
    device_id: Optional[str] = None
    max_steps: Optional[int] = None
    lang: Optional[str] = None

class TaskInfo(TaskCreate):
    id: str
    created_at: datetime
    updated_at: datetime

class TaskExecutionResponse(BaseModel):
    task_id: str
    execution_id: str
    status: str
    message: str

# Storage for tasks and executions
tasks: Dict[str, TaskInfo] = {}
executions: Dict[str, Dict] = {}  # execution_id -> {task_id, thread, agent, status, result}

# Create FastAPI app
app = FastAPI(title="Phone Agent Web UI", description="Web interface for managing phone automation tasks")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def load_tasks():
    """Load tasks from file if exists."""
    global tasks
    if os.path.exists("tasks.json"):
        with open("tasks.json", "r", encoding="utf-8") as f:
            tasks_data = json.load(f)
            for task_id, task_data in tasks_data.items():
                # Convert datetime strings back to datetime objects
                task_data['created_at'] = datetime.fromisoformat(task_data['created_at'])
                task_data['updated_at'] = datetime.fromisoformat(task_data['updated_at'])
                tasks[task_id] = TaskInfo(**task_data)

def save_tasks():
    """Save tasks to file."""
    tasks_data = {}
    for task_id, task in tasks.items():
        tasks_data[task_id] = task.dict()
        # Convert datetime objects to strings for JSON serialization
        tasks_data[task_id]['created_at'] = tasks_data[task_id]['created_at'].isoformat()
        tasks_data[task_id]['updated_at'] = tasks_data[task_id]['updated_at'].isoformat()
    
    with open("tasks.json", "w", encoding="utf-8") as f:
        json.dump(tasks_data, f, ensure_ascii=False, indent=2)

@app.on_event("startup")
async def startup_event():
    """Load tasks on startup."""
    load_tasks()

@app.on_event("shutdown")
async def shutdown_event():
    """Save tasks on shutdown."""
    save_tasks()

# Task Management APIs
@app.get("/tasks", response_model=List[TaskInfo])
async def list_tasks():
    """List all tasks."""
    return list(tasks.values())

@app.post("/tasks", response_model=TaskInfo)
async def create_task(task: TaskCreate):
    """Create a new task."""
    task_id = str(uuid.uuid4())
    now = datetime.now()
    task_info = TaskInfo(
        id=task_id,
        created_at=now,
        updated_at=now,
        **task.dict()
    )
    tasks[task_id] = task_info
    save_tasks()
    return task_info

@app.get("/tasks/{task_id}", response_model=TaskInfo)
async def get_task(task_id: str):
    """Get a specific task."""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return tasks[task_id]

@app.put("/tasks/{task_id}", response_model=TaskInfo)
async def update_task(task_id: str, task_update: TaskUpdate):
    """Update a specific task."""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Update only provided fields
    update_data = task_update.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(tasks[task_id], field, value)
    
    tasks[task_id].updated_at = datetime.now()
    save_tasks()
    return tasks[task_id]

@app.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    """Delete a specific task."""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    del tasks[task_id]
    save_tasks()
    return {"message": "Task deleted successfully"}

# Task Execution APIs
def run_task_execution(execution_id: str, task_info: TaskInfo):
    """Run the task in a separate thread."""
    try:
        executions[execution_id]["status"] = "running"
        
        # Create model and agent configs
        model_config = ModelConfig(
            base_url=task_info.base_url,
            model_name=task_info.model,
            api_key=task_info.apikey,
            lang=task_info.lang
        )
        
        agent_config = AgentConfig(
            max_steps=task_info.max_steps,
            device_id=task_info.device_id,
            lang=task_info.lang
        )
        
        # Create agent
        agent = PhoneAgent(model_config=model_config, agent_config=agent_config)
        executions[execution_id]["agent"] = agent
        
        # Run the task
        result = agent.run(task_info.description)
        executions[execution_id]["result"] = result
        executions[execution_id]["status"] = "completed"
        
    except Exception as e:
        executions[execution_id]["result"] = str(e)
        executions[execution_id]["status"] = "failed"

@app.post("/tasks/{task_id}/execute", response_model=TaskExecutionResponse)
async def execute_task(task_id: str):
    """Execute a task."""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task_info = tasks[task_id]
    execution_id = str(uuid.uuid4())
    
    # Create execution record
    executions[execution_id] = {
        "task_id": task_id,
        "thread": None,
        "agent": None,
        "status": "pending",
        "result": None
    }
    
    # Start execution in a separate thread
    execution_thread = threading.Thread(
        target=run_task_execution,
        args=(execution_id, task_info)
    )
    execution_thread.start()
    
    executions[execution_id]["thread"] = execution_thread
    
    return TaskExecutionResponse(
        task_id=task_id,
        execution_id=execution_id,
        status="started",
        message="Task execution started"
    )

@app.post("/tasks/{task_id}/stop", response_model=TaskExecutionResponse)
async def stop_task(task_id: str):
    """Stop a running task."""
    # Find running executions for this task
    for execution_id, execution in executions.items():
        if execution["task_id"] == task_id and execution["status"] == "running":
            # Note: In a real implementation, we would need a more sophisticated way to stop the agent
            # For now, we'll just mark it as stopped
            execution["status"] = "stopped"
            return TaskExecutionResponse(
                task_id=task_id,
                execution_id=execution_id,
                status="stopped",
                message="Task stop requested"
            )
    
    raise HTTPException(status_code=404, detail="No running execution found for this task")

@app.get("/executions/{execution_id}")
async def get_execution_status(execution_id: str):
    """Get the status of a task execution."""
    if execution_id not in executions:
        raise HTTPException(status_code=404, detail="Execution not found")
    
    execution = executions[execution_id]
    return {
        "execution_id": execution_id,
        "task_id": execution["task_id"],
        "status": execution["status"],
        "result": execution["result"]
    }

@app.get("/")
async def read_index():
    """Serve the main HTML page."""
    return FileResponse('static/index.html')


def main():
    """Run the web UI server."""
    uvicorn.run("web_ui:app", host="0.0.0.0", port=8000, reload=True)

if __name__ == "__main__":
    main()