# Contributing to TadoLocal

Thank you for your interest in contributing to TadoLocal! This document provides guidelines for developers.

## Development Setup

### Prerequisites
- Python 3.11 or later
- pip and virtualenv (recommended)

### Initial Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/ampscm/TadoLocal.git
   cd TadoLocal
   ```

2. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install runtime and development dependencies:
   ```bash
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   ```

4. Install the package in development mode:
   ```bash
   pip install -e .
   ```

## Running Tests

Run all tests:
```bash
pytest
```

Run tests with verbose output:
```bash
pytest -v
```

Run tests with coverage:
```bash
pytest --cov=tado_local --cov-report=html
```

## Code Quality

The project uses the following tools for code quality:

- **ruff**: Fast Python linter (excludes domoticz/)
- **flake8**: Additional linting
- **black**: Code formatting (configured in pyproject.toml)
- **pytest-asyncio**: Testing async code

### Running Code Quality Checks

```bash
ruff check .
flake8 tado_local/
```

## Project Structure

- `tado_local/` - Main package
- `domoticz/` - Domoticz plugin (uses namespace packaging)
- `tests/` - Unit tests
- `docs/` - Documentation
- `demos/` - Example scripts
- `systemd/` - Systemd service files

## Notes for Developers

- **Domoticz Plugin**: The `domoticz/plugin.py` can be used standalone. Tests use a mocked Domoticz module so the plugin can be tested without the Domoticz runtime.
- **Async Code**: Use `@pytest.mark.asyncio` for async tests (requires pytest-asyncio).
- **Namespace Packages**: The `domoticz/` folder uses namespace packaging to allow it to be imported separately.

## Open PRs

Please check for existing open PRs before working on new features to avoid conflicts.
