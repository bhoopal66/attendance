# Attendance System - Ubuntu 24.04 Deployment Guide

This is the **ONLY** deployment guide you need. Follow these steps exactly on your Ubuntu 24.04 server.

---

## What You Need Before Starting

- A fresh Ubuntu 24.04 server
- Root or sudo access
- Your server's IP address (example: 192.168.1.100)

---

## STEP 1: Connect to Your Ubuntu Server

Open a terminal and SSH into your server:
```bash
ssh your_username@YOUR_SERVER_IP
```

---

## STEP 2: Update Ubuntu & Install Required Software

Run these commands one by one:

```bash
# Update package list
sudo apt update

# Upgrade existing packages
sudo apt upgrade -y

# Install Python, MySQL, and other required packages
sudo apt install -y python3 python3-pip python3-venv python3-dev mysql-server libmysqlclient-dev pkg-config git
```

**Wait for each command to complete before running the next one.**

---

## STEP 3: Start MySQL Database Server

```bash
# Start MySQL service
sudo systemctl start mysql

# Enable MySQL to start on boot
sudo systemctl enable mysql
```

---

## STEP 4: Create the Database

```bash
# Open MySQL console
sudo mysql
```

You will see a `mysql>` prompt. Type these commands exactly:

```sql
CREATE DATABASE attendance_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

```sql
CREATE USER 'attendance_user'@'localhost' IDENTIFIED BY 'MySecurePassword123';
```

**⚠️ IMPORTANT: Replace `MySecurePassword123` with your own password and remember it!**

```sql
GRANT ALL PRIVILEGES ON attendance_db.* TO 'attendance_user'@'localhost';
```

```sql
FLUSH PRIVILEGES;
```

```sql
EXIT;
```

---

## STEP 5: Download the Application

```bash
# Create the application folder
sudo mkdir -p /var/www/attendance

# Give your user ownership
sudo chown $USER:$USER /var/www/attendance

# Go to the folder
cd /var/www/attendance

# Download the code from GitHub
git clone https://github.com/yadhumanikandan/attendance_system.git .
```

**Note:** Don't forget the `.` at the end of the git clone command!

---

## STEP 6: Create Python Virtual Environment

```bash
# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install required Python packages
pip install -r requirements.txt
```

After running `source venv/bin/activate`, you should see `(venv)` at the beginning of your command prompt.

---

## STEP 7: Create Configuration File

First, generate a secret key:
```bash
python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'
```

**Copy the long random string that appears!**

Now create the .env file:
```bash
cp .env.example .env
nano .env
```

Edit the file to look like this:
```
SECRET_KEY=paste-the-long-random-string-here
ALLOWED_HOSTS=localhost,127.0.0.1,YOUR_SERVER_IP
DB_PASSWORD=MySecurePassword123
```

**Replace:**
- `paste-the-long-random-string-here` → The key you generated above
- `YOUR_SERVER_IP` → Your actual server IP (e.g., 192.168.1.100)
- `MySecurePassword123` → The MySQL password from Step 4

Press `Ctrl+O` then `Enter` to save, then `Ctrl+X` to exit nano.

---

## STEP 8: Setup the Database Tables

Run these commands one by one:

```bash
# Make sure virtual environment is active
source venv/bin/activate

# Create database tables
DJANGO_SETTINGS_MODULE=attendance_project.settings.production python manage.py migrate
```

You should see output like:
```
Operations to perform:
  Apply all migrations...
Running migrations:
  Applying attendance.0001_initial... OK
  ...
```

---

## STEP 9: Create Admin User

```bash
DJANGO_SETTINGS_MODULE=attendance_project.settings.production python manage.py createsuperuser
```

Enter when prompted:
- **Username:** admin (or your preferred username)
- **Email:** your@email.com
- **Password:** Choose a strong password

---

## STEP 10: Collect Static Files

```bash
DJANGO_SETTINGS_MODULE=attendance_project.settings.production python manage.py collectstatic --noinput
```

---

## STEP 11: Test the Application

```bash
DJANGO_SETTINGS_MODULE=attendance_project.settings.production gunicorn --bind 0.0.0.0:8000 attendance_project.wsgi:application
```

Now open a web browser on any computer in your network and go to:
```
http://YOUR_SERVER_IP:8000
```

You should see the login page! Log in with the admin credentials from Step 9.

**Press `Ctrl+C` in the terminal to stop the test server.**

---

## STEP 12: Setup Automatic Startup

This makes the application start automatically when the server boots.

```bash
# Copy service file to system folder
sudo cp deployment/attendance.service /etc/systemd/system/

# Change ownership of application files
sudo chown -R www-data:www-data /var/www/attendance

# Reload systemd
sudo systemctl daemon-reload

# Enable automatic startup on boot
sudo systemctl enable attendance

# Start the application now
sudo systemctl start attendance
```

---

## STEP 13: Check if Application is Running

```bash
sudo systemctl status attendance
```

You should see:
```
● attendance.service - Attendance System Gunicorn Daemon
     Loaded: loaded (/etc/systemd/system/attendance.service; enabled)
     Active: active (running) since...
```

---

## STEP 14: Open Firewall Port

```bash
sudo ufw allow 8000/tcp
```

---

## ✅ DONE!

Your attendance system is now running!

Access it from any computer on your network:
```
http://YOUR_SERVER_IP:8000
```

---

## Troubleshooting Commands

### View application logs
```bash
sudo journalctl -u attendance -f
```
Press `Ctrl+C` to stop viewing logs.

### Restart application
```bash
sudo systemctl restart attendance
```

### Stop application
```bash
sudo systemctl stop attendance
```

### Check MySQL is running
```bash
sudo systemctl status mysql
```

---

## How to Update the Application Later

When you make changes and push to GitHub, run these on the server:

```bash
cd /var/www/attendance
source venv/bin/activate
git pull origin main
DJANGO_SETTINGS_MODULE=attendance_project.settings.production python manage.py migrate
DJANGO_SETTINGS_MODULE=attendance_project.settings.production python manage.py collectstatic --noinput
sudo systemctl restart attendance
```

---

## Quick Reference

| Item | Value |
|------|-------|
| Application URL | http://YOUR_SERVER_IP:8000 |
| Application Folder | /var/www/attendance |
| Config File | /var/www/attendance/.env |
| Database Name | attendance_db |
| Database User | attendance_user |
| Service Name | attendance |
/cle