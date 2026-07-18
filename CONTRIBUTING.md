# Contributing

Murphy's Bench is built and maintained by one person, around the daily work of one repair shop. Issues and pull requests are welcome, but review and response times are best-effort, not guaranteed.

## Before you open a PR

For anything beyond a small fix, open an issue first to talk through the approach. This project is deliberately conservative about scope — it aims to do the daily repair-shop workflow well, not grow into a general-purpose PSA. A feature that makes sense for your shop may not fit here; that's not a reflection on the idea.

## Running the project locally

```bash
cd murphys-bench
source venv/bin/activate
python manage.py runserver
```

See `INSTALL.md` for a full from-scratch setup.

## Tests

Any change touching data — deletion, billing state, ticket/work-order lifecycle, email routing, number generation, or permissions — needs a test that locks in the behavior. Run the suite with:

```bash
python -m pytest
```

CI runs the same suite plus `manage.py check` on every push and PR; both need to be green before merge.

## Code style

Match the existing patterns in the file you're touching rather than introducing a new one. This is a server-rendered Django app (HTMX + Alpine, no build step, no SPA framework) — new features should stay in that shape unless there's a strong reason not to.
