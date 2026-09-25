.PHONY: dev dev-local test test-local smoke firmware-build firmware-flash firmware-monitor

dev:
	docker compose up --build

dev-local:
	PYTHONPATH=backend .venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 --ws-ping-timeout 60

test:
	docker compose run --rm backend python -m unittest discover -s tests -v

test-local:
	PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -v

smoke:
	./scripts/smoke_test.sh

firmware-build:
	./scripts/idf.sh build

firmware-flash:
	./scripts/idf.sh -p $${PORT:?Set PORT=/dev/cu.usbmodem...} flash

firmware-monitor:
	./scripts/idf.sh -p $${PORT:?Set PORT=/dev/cu.usbmodem...} monitor
