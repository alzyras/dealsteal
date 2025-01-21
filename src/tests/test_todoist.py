import os

import pytest

from dealsteal.todoist import TodoistClient

# Load environment variables
TODOIST_TOKEN = os.getenv("TODOIST_TOKEN")
TODOIST_PROJECT = os.getenv("TODOIST_PROJECT")


@pytest.fixture
def todoist_client():
    """Fixture to initialize the Todoist client."""
    assert TODOIST_TOKEN is not None, "TODOIST_TOKEN environment variable is not set."
    return TodoistClient(TODOIST_TOKEN)


def test_get_projects(todoist_client):
    """Test retrieving projects from Todoist."""
    projects = todoist_client.get_projects()
    assert projects is not None, "Failed to retrieve projects."
    assert isinstance(projects, list), "Projects should be returned as a list."
    assert any(
        project["id"] == str(TODOIST_PROJECT) for project in projects
    ), f"Project with ID {TODOIST_PROJECT} not found."


def test_task_lifecycle(todoist_client):
    """Test creating, retrieving, and deleting a task."""
    # Create a new task
    task_data = {
        "content": "Test Task",
        "project_id": TODOIST_PROJECT,
    }
    response = todoist_client.submit_task(
        title=task_data["content"], project_id=task_data["project_id"]
    )
    assert response is not None, "Failed to create task."
    task_id = response["id"]

    # Retrieve the task
    task = todoist_client.get_task(task_id)
    assert task is not None, "Failed to retrieve the created task."
    assert task["content"] == task_data["content"], "Task content does not match."

    # Delete the task
    delete_response = todoist_client.delete_task(task_id)
    assert delete_response is True, "Unexpected response on task deletion."
    print("Task deleted successfully.")


if __name__ == "__main__":
    pytest.main()
