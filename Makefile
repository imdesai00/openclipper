.PHONY: env up down logs ps test lint

env: ## create .env with generated secrets (never overwrites)
	@bash scripts/make-env.sh

up: env ## build and start everything, wait until healthy
	docker compose up -d --build --wait

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

ps:
	docker compose ps

test:
	cd backend && uv run pytest -q
	cd frontend && npm test

lint:
	cd backend && uv run ruff check app tests migrations && uv run ruff format --check app tests migrations && uv run mypy
	cd frontend && npm run lint
