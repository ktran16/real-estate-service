# Makefile for the self-hosted Metabase container (Da Nang real estate marts).
# Run from the metabase/ directory:  make help
#
# Metabase serves http://localhost:3001 and reads ../data/danang.duckdb (read-only).
# DuckDB is single-writer: stop Metabase before running the scrape/transform pipeline.

COMPOSE   := docker compose
CONTAINER := danang-metabase
URL       := http://localhost:3001

.DEFAULT_GOAL := help

.PHONY: help setup driver up down stop start restart logs ps status health \
        fix-perms shell clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: driver up ## First-time setup: download driver + start container

driver: ## Download the pinned DuckDB driver jar + set dir perms
	./fetch-driver.sh

up: ## Start Metabase in the background
	$(COMPOSE) up -d
	@echo "Metabase starting -> $(URL)  (run 'make logs' to watch boot)"

down: ## Stop and remove the container
	$(COMPOSE) down

stop: ## Stop the container (keeps it; frees the DuckDB file for the pipeline)
	$(COMPOSE) stop

start: ## Start a previously-stopped container
	$(COMPOSE) start

restart: ## Restart the container (e.g. after dropping in a new driver jar)
	$(COMPOSE) restart

logs: ## Follow the container logs
	$(COMPOSE) logs -f

ps: ## Show container status
	$(COMPOSE) ps

status: ps ## Alias for ps

health: ## Check the Metabase API health endpoint + Postgres serving DB
	@printf "metabase health: "; curl -s -o /dev/null -w "%{http_code}\n" $(URL)/api/health
	@printf "postgres: "; docker exec danang-postgres pg_isready -U danang -d danang 2>/dev/null || echo "not ready"

fix-perms: ## Make plugins/ + metabase-data/ writable by the metabase user (uid 2000), then restart
	docker exec -u root $(CONTAINER) chmod 777 /plugins /metabase-data
	$(COMPOSE) restart
	@echo "Permissions fixed and Metabase restarted; run 'make health' to confirm the driver loaded."

shell: ## Open a root shell inside the container
	docker exec -it -u root $(CONTAINER) /bin/sh

clean: ## Stop the container and delete the driver jar + Metabase app state (DESTRUCTIVE)
	$(COMPOSE) down
	rm -rf plugins metabase-data
	@echo "Removed plugins/ and metabase-data/. Run 'make setup' to start fresh."
