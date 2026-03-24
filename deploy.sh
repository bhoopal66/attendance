#!/bin/bash
set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
RESET='\033[0m'

step() { echo -e "\n${CYAN}${BOLD}==> $1${RESET}"; }
success() { echo -e "${GREEN}✔  $1${RESET}"; }
fail() { echo -e "${RED}✘  ERROR: $1${RESET}"; exit 1; }

echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD}        TCR Attendance — Deploy          ${RESET}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

step "Activating virtual environment..."
source venv/bin/activate && success "Virtual environment activated"

step "Pulling latest code from origin/main..."
git pull origin main && success "Code updated"

step "Running database migrations..."
DJANGO_SETTINGS_MODULE=attendance_project.settings.production python3 manage.py migrate --verbosity=2 && success "Migrations complete"

step "Collecting static files..."
DJANGO_SETTINGS_MODULE=attendance_project.settings.production python3 manage.py collectstatic --noinput --verbosity=2 && success "Static files collected"

step "Restarting attendance service..."
sudo systemctl restart attendance && success "Service restarted"

echo -e "\n${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${GREEN}${BOLD}   ✔  Deployment complete!               ${RESET}"
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"
