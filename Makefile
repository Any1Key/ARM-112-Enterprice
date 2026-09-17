up:
	docker compose up -d --build
down:
	docker compose down
logs:
	docker compose logs -f app
voip:
	docker compose --profile voip up -d --build
ai:
	docker compose --profile ai up -d --build
backup:
	docker compose exec -T backup sh /backup.sh
verify-backup:
	docker compose exec -T backup sh /verify.sh
restore:
	@echo "See docs/OPERATIONS.md: custom-format pg_restore, stop app before restoring."

test:
	docker compose run --rm --no-deps -T -e PYTHONPATH=/app -v "$(CURDIR)/app:/app/app:ro" -v "$(CURDIR)/tests:/app/tests:ro" -v "$(CURDIR)/services:/app/services:ro" app python -m pytest -q
ocr:
	docker build -f tools/Dockerfile.ocr -t arm112-ocr .
	docker run --rm -v "$(CURDIR)/source_materials:/sources:ro" -v "$(CURDIR)/data/imports:/output" arm112-ocr "/sources/Билеты- задачи по C 112 . АГС_ГСИ.pdf" /output/tickets.json
