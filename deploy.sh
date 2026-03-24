#!/bin/bash
set -ex

echo "==> Activating virtual environment..."
source venv/bin/activate

echo "==> Pulling latest code from origin/main..."
git pull origin main

echo "==> Running database migrations..."
DJANGO_SETTINGS_MODULE=attendance_project.settings.production python3 manage.py migrate --verbosity=2

echo "==> Collecting static files..."
DJANGO_SETTINGS_MODULE=attendance_project.settings.production python3 manage.py collectstatic --noinput --verbosity=2

echo "==> Restarting attendance service..."
sudo systemctl restart attendance

echo "==> Done. Deployment complete."
