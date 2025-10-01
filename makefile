env:
	pip install uv
	pip install pre-commit
	uv sync --extra dev
	pre-commit install

requirements:
	uv pip compile pyproject.toml --output-file=requirements.txt

