.PHONY: dev-backend dev-frontend dev-db install docker-build docker-run db-setup

# Start Postgres locally via Docker
dev-db:
	docker run -d --name judgetable-db \
		-e POSTGRES_USER=judgetable \
		-e POSTGRES_PASSWORD=judgetable \
		-e POSTGRES_DB=judgetable \
		-p 5432:5432 \
		postgres:16-alpine

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

# Stop and remove the dev database container
db-stop:
	docker stop judgetable-db && docker rm judgetable-db
