# tallyhub

Order, pricing, tax and billing service (in-memory storage, framework-free HTTP handlers).

- `tallyhub/app.py` wires every service; `tallyhub/seed.py` loads demo data.
- HTTP API: `tallyhub.api.handlers.ApiApp(app).handle(method, path, body, headers)`.
- CLI: `python3 -m tallyhub.cli place --customer c_alice --item TEE-RED:2 --region CA`.
- Settings: `tallyhub/core/settings.py` (`DEFAULTS`), overridable via `build_app(overrides)`.

Run the tests with `python3 -m unittest discover -s tests -t .`
