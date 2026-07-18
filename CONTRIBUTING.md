# Contributing

Murphy's Bench is built and maintained by one person around the daily work of one repair shop. Issues and pull requests are welcome, but review and response times are best-effort.

## Before You Open a PR

For anything beyond a small fix, open an issue first so the approach can be discussed.

Murphy's Bench is deliberately conservative about scope. It is meant to support the daily work of a small MSP or repair shop, not grow into a general-purpose PSA. A feature that makes sense for your shop may not fit the project, and that is not a judgment on the idea itself.

## Running the Project Locally

The installation script handles the application dependencies and initial setup. Once the project is installed:

```bash
cd murphys-bench
source venv/bin/activate
python manage.py runserver
```

See `INSTALL.md` for the complete setup process.

## Tests

Changes affecting important behavior need tests. This includes changes involving:

- deletion
- billing state
- ticket and work-order lifecycle
- email routing
- number generation
- permissions

Run the test suite with:

```bash
python -m pytest
```

CI runs the same suite along with `manage.py check`. Both must pass before a change can be merged.

## Database Migrations

Include migrations when a model change requires them. Keep migrations limited to the change being made, and do not rewrite or remove existing migrations without discussing it first.

## Code Style

Follow the existing patterns in the files you are changing rather than introducing a new structure or dependency without a clear need.

Murphy's Bench is a server-rendered Django application using HTMX and Alpine.js, not a SPA or separate frontend application. New features should generally stay within that structure unless there is a strong reason not to.
