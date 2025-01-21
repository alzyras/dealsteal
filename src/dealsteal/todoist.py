import logging
import os

import requests

LOGGER = logging.getLogger(__name__)


class TodoistClient:
    def __init__(self, api_token: str):
        """
        Initialize the Todoist client.

        :param api_token: Your Todoist API token.
        """
        self.api_token = api_token
        self.url = "https://api.todoist.com/rest/v2/tasks"
        self.headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }
        self.items_file = "store/items.txt"

    def _is_item_used(self, item_id: str) -> bool:
        """
        Check if an item has already been used.

        :param item_id: The ID of the item to check.
        :return: True if the item has been used, False otherwise.
        """
        if not os.path.exists(self.items_file):
            return False
        with open(self.items_file, "r") as file:
            used_items = file.read().splitlines()
        return item_id in used_items

    def _mark_item_as_used(self, item_id: str) -> None:
        """
        Mark an item as used by appending its ID to the items file.

        :param item_id: The ID of the item to mark as used.
        """
        with open(self.items_file, "a") as file:
            file.write(f"{item_id}\n")

    def get_projects(self) -> list:
        """
        Get all projects from Todoist.

        :return: List of projects, or None if the request failed.
        """
        projects_url = "https://api.todoist.com/rest/v2/projects"
        response = requests.get(projects_url, headers=self.headers)

        if response.status_code == 200:
            return response.json()
        else:
            LOGGER.error(
                f"Failed to get projects: {response.status_code}, {response.text}"
            )
            return None

    def get_task(self, task_id: str) -> dict:
        """
        Get a task from Todoist by its ID.

        :param task_id: The ID of the task.
        :return: Response JSON from Todoist API, or None if the request failed.
        """
        task_url = f"https://api.todoist.com/rest/v2/tasks/{task_id}"
        response = requests.get(task_url, headers=self.headers)

        if response.status_code == 200:
            return response.json()
        else:
            LOGGER.error(f"Failed to get task: {response.status_code}, {response.text}")
            return None

    def delete_task(self, task_id: str) -> bool:
        """
        Delete a task from Todoist by its ID.

        :param task_id: The ID of the task.
        :return: True if the task was successfully deleted, False otherwise.
        """
        task_url = f"https://api.todoist.com/rest/v2/tasks/{task_id}"
        response = requests.delete(task_url, headers=self.headers)

        if response.status_code == 204:
            LOGGER.info("Task successfully deleted.")
            return True
        else:
            LOGGER.error(
                f"Failed to delete task: {response.status_code}, {response.text}"
            )
            return False

    def submit_task(
        self,
        title: str,
        description: str = None,
        due_date: str = None,
        project_id: str = None,
        item_id: str = None,
    ) -> dict:
        """
        Submit a task to Todoist.

        :param title: The title of the task.
        :param description: Optional. The description of the task.
        :param due_date: Optional. The due date for the task (e.g., "2025-01-08T12:00:00Z").
        :param project_id: Optional. The ID of the project to add the task to.
        :param item_id: Optional. The ID of the item to check if it was already used.
        :return: Response JSON from Todoist API, or None if the task was not submitted.
        """
        if item_id and self._is_item_used(item_id):
            LOGGER.warning(f"Item {item_id} was already used. Task not submitted.")
            return None

        data = {"content": title}

        if description:
            data["description"] = description

        if due_date:
            data["due_date"] = due_date

        if project_id:
            data["project_id"] = project_id

        response = requests.post(self.url, json=data, headers=self.headers)

        if response.status_code in [200, 204]:
            LOGGER.info("Task successfully added.")
            if item_id:
                self._mark_item_as_used(item_id)
        else:
            LOGGER.error(f"Failed to add task: {response.status_code}, {response.text}")

        return response.json()


if __name__ == "__main__":
    API_TOKEN = os.environ.get(
        "TODOIST_TOKEN"
    )  # Replace with your actual Todoist API token

    if not API_TOKEN:
        LOGGER.error("Error: TODOIST_TOKEN is not set in environment variables.")
        exit(1)

    client = TodoistClient(API_TOKEN)

    TITLE = "Complete the Python project"
    DESCRIPTION = "Finish the class-based Todoist script and test it."
    DUE_DATE = "2025-01-10T15:00:00Z"  # Optional. Set to None if not needed
    PROJECT_ID = os.environ.get(
        "TODOIST_PROJECT"
    )  # Optional. Replace with your project ID if needed
    ITEM_ID = "unique_item_id_123"  # Replace with your actual item ID if needed

    response = client.submit_task(TITLE, DESCRIPTION, DUE_DATE, PROJECT_ID, ITEM_ID)
    LOGGER.info(response)

    # Example usage of get_projects
    projects = client.get_projects()
    if projects:
        for project in projects:
            LOGGER.info(f"Project: {project['name']}, ID: {project['id']}")
