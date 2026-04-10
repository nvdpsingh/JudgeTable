.PHONY: dev-backend dev-frontend install docker-build docker-run

dev-backend:
	cd backend && uvicorn main:app --reload --host 0.0.0.0 --port 8000

dev-frontend:
	cd frontend && npm run dev

install:
	cd backend && pip install -r requirements.txt
	cd frontend && npm install

docker-build:
	docker compose build

docker-run:
	docker compose up
