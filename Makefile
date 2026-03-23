# ─────────────────────────────────────────────────────────────
# RBAC Subscription Platform — Makefile
# ─────────────────────────────────────────────────────────────

.PHONY: help dev down build test lint format typecheck \
        migrate makemigrations shell logs clean \
        seed-permissions seed-data seed-faker seed-plans superuser

# Default: show help
help:
	@echo "Available commands:"
	@echo "  make dev             Start development server (docker-compose up)"
	@echo "  make down            Stop all containers"
	@echo "  make build           Rebuild Docker images"
	@echo "  make migrate         Run database migrations"
	@echo "  make makemigrations  Create new migrations"
	@echo "  make seed-permissions  Load permissions and system roles fixtures"
	@echo "  make seed-data       Generate dev seed data (tenants, users)"
	@echo "  make seed-faker      Fill all app tables with realistic Faker data"
	@echo "  make superuser       Create a Django superuser"
	@echo "  make test            Run test suite"
	@echo "  make lint            Run ruff linter"
	@echo "  make format          Auto-format code with ruff"
	@echo "  make typecheck       Run mypy type checker"
	@echo "  make shell           Open Django shell"
	@echo "  make logs            Tail container logs"
	@echo "  make clean           Remove containers, volumes and cache files"

# ── Docker ────────────────────────────────────────────────────
dev:
	docker-compose up

down:
	docker-compose down

build:
	docker-compose build --no-cache

logs:
	docker-compose logs -f

# ── Database ──────────────────────────────────────────────────
migrate:
	docker-compose exec django python manage.py migrate

makemigrations:
	docker-compose exec django python manage.py makemigrations

seed-permissions:
	docker-compose exec django python manage.py seed_permissions

seed-data:
	docker-compose exec django python manage.py seed_dev_data

seed-faker:
	docker-compose exec django python manage.py seed_faker_data

seed-plans:
	docker-compose exec django python manage.py seed_plans

superuser:
	docker-compose exec django python manage.py createsuperuser

# ── Development ───────────────────────────────────────────────
shell:
	docker-compose exec django python manage.py shell_plus

# ── Testing ───────────────────────────────────────────────────
test:
	docker-compose exec django python manage.py test --verbosity=2

test-coverage:
	docker-compose exec django sh -c "coverage run manage.py test && coverage report --min-coverage=80"

# ── Code Quality ──────────────────────────────────────────────
lint:
	docker-compose exec django ruff check .

format:
	docker-compose exec django ruff format .

typecheck:
	docker-compose exec django mypy . --ignore-missing-imports

# ── Cleanup ───────────────────────────────────────────────────
clean:
	docker-compose down -v --remove-orphans
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	@echo "Clean done."

# ── Env check ─────────────────────────────────────────────────
env-check:
	@test -f .env || (echo "ERROR: .env file not found. Copy .env.example to .env" && exit 1)
	@echo ".env exists OK"
