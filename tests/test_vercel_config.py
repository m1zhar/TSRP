"""Keep Vercel Services, Python routing, and CI settings aligned."""

import asyncio
import importlib
import json
import re
import tomllib
import unittest
from pathlib import Path

from api_helpers import JsonApiHandler
from api.index import ROUTES


ROOT = Path(__file__).resolve().parents[1]


class VercelConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))

    def test_services_config_keeps_framework_settings_service_local(self):
        services = self.config.get("services", {})
        self.assertEqual(
            services.get("frontend"),
            {"root": "frontend/", "framework": "nextjs"},
        )
        self.assertEqual(
            services.get("backend"),
            {
                "root": "backend/",
                "framework": "python",
                "entrypoint": "api.index:app",
                "functions": {"api/index.py": {"maxDuration": 60}},
            },
        )
        backend_project = tomllib.loads(
            (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
        )
        entrypoint = services["backend"]["entrypoint"]
        self.assertEqual(backend_project["tool"]["vercel"]["entrypoint"], entrypoint)
        module_name, app_name = entrypoint.split(":", 1)
        module = importlib.import_module(module_name)
        self.assertTrue(callable(getattr(module, app_name, None)))
        self.assertTrue(asyncio.iscoroutinefunction(getattr(module, app_name)))
        self.assertNotIn("framework", self.config)
        self.assertNotIn("functions", self.config)
        rewrites = self.config.get("rewrites", [])
        self.assertEqual(
            rewrites,
            [
                {"source": "/api/auth/:path*", "destination": {"service": "frontend"}},
                {"source": "/api/backend-token", "destination": {"service": "frontend"}},
                {"source": "/api/:path*", "destination": {"service": "backend"}},
                {"source": "/(.*)", "destination": {"service": "frontend"}},
            ],
        )

    def test_frontend_node_engine_pins_a_vercel_supported_major(self):
        package = json.loads(
            (ROOT / "frontend/package.json").read_text(encoding="utf-8")
        )
        self.assertEqual(package["engines"]["node"], "22.x")

    def test_dependabot_directories_match_project_manifests(self):
        source = (ROOT / ".github/dependabot.yml").read_text(encoding="utf-8")
        updates = re.findall(
            r"(?m)^  - package-ecosystem: ([^\n]+)\n    directory: ([^\n]+)",
            source,
        )
        self.assertEqual(
            updates,
            [
                ("github-actions", "/"),
                ("npm", "/frontend"),
                ("uv", "/backend"),
            ],
        )
        self.assertTrue((ROOT / "frontend/package.json").is_file())
        self.assertTrue((ROOT / "backend/pyproject.toml").is_file())
        self.assertTrue((ROOT / "backend/uv.lock").is_file())

    def test_backend_python_lock_matches_project_requirements(self):
        project = tomllib.loads(
            (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
        )["project"]
        self.assertEqual(project["requires-python"], ">=3.12,<3.13")
        lock = tomllib.loads((ROOT / "backend/uv.lock").read_text(encoding="utf-8"))
        self.assertEqual(lock["requires-python"], "==3.12.*")
        lock_packages = {item["name"]: item for item in lock["package"]}
        project_package = lock_packages["tsrp-api"]
        locked_direct = {
            item["name"]: item["specifier"]
            for item in project_package["metadata"]["requires-dist"]
        }
        manifest_direct = {}
        for dependency in project["dependencies"]:
            match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)", dependency)
            self.assertIsNotNone(match, f"Runtime dependency must stay exactly pinned: {dependency}")
            manifest_direct[match.group(1).lower().replace("_", "-")] = f"=={match.group(2)}"
        self.assertEqual(locked_direct, manifest_direct)
        self.assertEqual(
            {item["name"] for item in project_package["dependencies"]},
            set(manifest_direct),
        )
        for package in lock["package"]:
            if package["name"] == "tsrp-api":
                continue
            artifacts = [package.get("sdist"), *package.get("wheels", [])]
            self.assertTrue(artifacts, f"{package['name']} has no locked distribution")
            for artifact in artifacts:
                if artifact is not None:
                    self.assertRegex(artifact["hash"], r"^sha256:[0-9a-f]{64}$")

    def test_frontend_security_headers_remain_configured(self):
        config = (ROOT / "frontend/next.config.ts").read_text(encoding="utf-8")
        self.assertIn('source: "/:path*"', config)
        for setting in (
            'key: "X-Content-Type-Options", value: "nosniff"',
            'key: "X-Frame-Options", value: "DENY"',
            'key: "Referrer-Policy", value: "strict-origin-when-cross-origin"',
            'key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()"',
        ):
            with self.subTest(setting=setting):
                self.assertIn(setting, config)
        self.assertIn('process.env.NODE_ENV === "production"', config)
        self.assertNotIn('key: "Content-Security-Policy"', config)
        self.assertIn('key: "Strict-Transport-Security"', config)

        proxy = (ROOT / "frontend/proxy.ts").read_text(encoding="utf-8")
        self.assertIn('requestHeaders.set("Content-Security-Policy", policy)', proxy)
        self.assertIn('response.headers.set("Content-Security-Policy", policy)', proxy)
        self.assertIn("'strict-dynamic'", proxy)
        self.assertIn("'nonce-${nonce}'", proxy)

    def test_asgi_route_map_covers_every_api_handler(self):
        route_files = sorted(
            path for path in (ROOT / "backend/api").glob("*.py")
            if path.name != "index.py" and path.name != "__init__.py"
        )
        route_paths = {f"/api/{path.stem}" for path in route_files}
        self.assertEqual(set(ROUTES), route_paths)
        self.assertTrue(route_files, "Expected API handlers in backend/api")
        for path in route_files:
            with self.subTest(route=path.name):
                module = importlib.import_module(f"api.{path.stem}")
                handler_class = getattr(module, "handler", None)
                self.assertIsInstance(handler_class, type)
                self.assertTrue(issubclass(handler_class, JsonApiHandler))

    def test_asgi_route_methods_match_each_handler_allowlist(self):
        for route, callbacks in ROUTES.items():
            module = importlib.import_module(f"api.{route.rsplit('/', 1)[-1]}")
            handler_class = module.handler
            allowed = {
                method.strip().upper()
                for method in handler_class.allowed_methods.split(",")
            }
            with self.subTest(route=route):
                self.assertEqual(set(callbacks), allowed)
                for method, callback in callbacks.items():
                    self.assertIs(callback, getattr(handler_class, f"do_{method}"))

    def test_workflow_actions_are_pinned_to_full_commit_shas(self):
        workflow = (ROOT / ".github/workflows/quality.yml").read_text(encoding="utf-8")
        references = re.findall(
            r"^\s*uses:\s*[^@\s]+@([0-9a-f]{40})\s+#\s+v\S+",
            workflow,
            re.MULTILINE,
        )
        self.assertEqual(len(references), 3)
        self.assertIn("working-directory: frontend", workflow)
        self.assertIn("PYTHONPATH: backend", workflow)
        self.assertIn("uv lock --check", workflow)
        self.assertIn("uv sync --locked --no-install-project", workflow)
        self.assertIn("uv export --locked", workflow)
        self.assertIn('pip-audit -r "$RUNNER_TEMP/tsrp-python-requirements.txt"', workflow)
        self.assertNotIn("backend/requirements.txt", workflow)

    def test_secret_files_are_ignored_and_environment_template_is_inert(self):
        ignore_rules = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".env", ignore_rules)
        self.assertIn(".env.*", ignore_rules)
        self.assertIn("!.env.example", ignore_rules)
        self.assertIn("*.pem", ignore_rules)
        self.assertIn("*.key", ignore_rules)

        template = (ROOT / ".env.example").read_text(encoding="utf-8")
        active_assignments = [
            line for line in template.splitlines()
            if "=" in line and not line.lstrip().startswith("#")
        ]
        self.assertEqual(active_assignments, [], "The committed example must not activate credentials or settings.")


if __name__ == "__main__":
    unittest.main()
