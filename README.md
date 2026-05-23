# Square Schedule Manager
**Professional Team Scheduling Application for Square**

A user-friendly desktop/Docker application that allows your team to bulk upload, verify, and publish team schedules to Square with a guided workflow.

---

## 📋 TABLE OF CONTENTS

1. [Features](#features)
2. [Quick Start](#quick-start)
3. [Setup Instructions](#setup-instructions)
4. [Docker Deployment](#docker-deployment)
5. [Configuration](#configuration)
6. [Usage Guide](#usage-guide)
7. [CSV Format](#csv-format)
8. [Troubleshooting](#troubleshooting)

---

## ✨ FEATURES

### Core Functionality
- ✅ **CSV Upload & Bulk Processing** - Upload team schedules via CSV
- ✅ **Guided Workflow** - Upload → Verify → Approve → Process
- ✅ **Change Detection** - See what's different from previous uploads
- ✅ **Data Lookups** - Maintain Location, Job, and Team Member database
- ✅ **Authentication** - Simple username/password login
- ✅ **Professional UI** - Clean, modern interface
- ✅ **Audit Trail** - Complete history of all uploads and changes
- ✅ **Error Handling** - Clear error messages and validation
- ✅ **Windows & Docker** - Runs on Windows or containerized

### Administrator Features
- 🔧 Manage Location, Job, and Team Member reference data
- 👥 Create/manage user accounts
- ⚙️ Configure Square API settings
- 📊 View upload history and audit logs

---

## ⚡ QUICK START

### Option 1: Windows (Python)

#### Prerequisites
- Windows 10/11
- Python 3.9+ (download from python.org)
- Your Square API Access Token

#### Installation
```bash
# 1. Extract the application files to a folder
cd C:\SquareScheduleManager

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set environment variable for your Square token
set SQUARE_ACCESS_TOKEN=your_token_here

# 4. Run the application
python app.py
```

**Access the application:**
- Open browser to: http://localhost:5000
- Login: admin / admin123
- **⚠️ Change password immediately!**

### Option 2: Docker

#### Prerequisites
- Docker Desktop installed (docker.com)
- Your Square API Access Token

#### Pull from Docker Hub (easiest)

The image is published automatically on every push to `main`:

```bash
docker pull geoffvaughan/square-schedule-upload:latest

docker run -d --name square-schedule-manager \
  -p 5000:5000 \
  -e SQUARE_ENVIRONMENT=sandbox \
  -e SANDBOX_ACCESS_TOKEN=your_sandbox_token \
  -e PRODUCTION_ACCESS_TOKEN=your_production_token \
  -e SECRET_KEY=your_secret_key \
  -v $(pwd)/data:/app/data \
  geoffvaughan/square-schedule-upload:latest
```

From Docker Desktop's UI: search for `geoffvaughan/square-schedule-upload`, click **Pull**, then **Run** with the env vars above.

#### Or build from source

```bash
# 1. Create .env file
cat > .env << EOF
SQUARE_ENVIRONMENT=sandbox
SANDBOX_ACCESS_TOKEN=your_sandbox_token
PRODUCTION_ACCESS_TOKEN=your_production_token
SECRET_KEY=your_secret_key_here
EOF

# 2. Run with Docker Compose
docker-compose up -d

# 3. Access application
# Open browser to: http://localhost:5000
```

**Stop the application:**
```bash
docker-compose down
```

---

## Running Tests

The repo ships with a `pytest` suite covering auth, settings CRUD, admin gating, and the upload → verify → process flow (Square API mocked):

```bash
pip install -r requirements.txt
pytest -v
```

A GitHub Actions workflow (`.github/workflows/ci.yml`) runs the same suite on every push and PR against Python 3.10 and 3.11.

A `sample_schedule.csv` is included in the repo root for manual testing.

---

## 🔧 SETUP INSTRUCTIONS

### Step 1: Get Your Square IDs

You'll need to gather IDs from your Square Dashboard and enter them into the application.

#### Location ID
```
1. Go to Square Dashboard → Settings → Locations
2. Click the location name
3. Copy the ID from the URL (looks like: PAA1RJZZKXBFG)
4. In the app: Settings → Locations → Add
```

#### Job ID
```
1. Go to Square Dashboard → Staff → Jobs
2. Click the job name (e.g., "Barista")
3. Copy the ID from the URL
4. In the app: Settings → Jobs → Add
```

#### Team Member ID
```
1. Go to Square Dashboard → Staff → Team Members
2. Click an employee name
3. Copy the ID from the URL
4. In the app: Settings → Team Members → Add
```

### Step 2: Create Your CSV File

See **CSV Format** section below for template.

Example:
```csv
employee_name,job_title,location_name,shift_date,start_time,end_time,timezone_offset
John Smith,Barista,Arlington,2025-05-23,09:00,17:00,-04:00
Jane Doe,Cashier,Arlington,2025-05-23,10:00,18:00,-04:00
Bob Johnson,Manager,Alexandria,2025-05-23,06:00,14:00,-04:00
```

### Step 3: Upload & Process

1. **Upload CSV**
   - Click "Upload Schedule"
   - Select your CSV file
   - Click "Upload"

2. **Verify Data**
   - Review the preview
   - Check for any errors (red rows)
   - See what locations/jobs are mapped
   - Identify additions/changes

3. **Approve**
   - Click "Approve & Process"
   - Confirm you want to proceed

4. **Results**
   - See success/error count
   - View shift IDs created in Square
   - Download detailed report if needed

---

## 🐳 DOCKER DEPLOYMENT

### Production Deployment

#### Using Docker Compose (Recommended)

**1. Create project folder:**
```bash
mkdir square-scheduler
cd square-scheduler
```

**2. Copy files:**
```
Dockerfile
docker-compose.yml
app.py
database.py
square_api.py
requirements.txt
templates/
static/
```

**3. Create .env file:**
```bash
# .env
SQUARE_ACCESS_TOKEN=YOUR_SQUARE_TOKEN_HERE
SECRET_KEY=your-very-secret-key-change-this
FLASK_ENV=production
```

**4. Build and run:**
```bash
docker-compose up -d
```

**5. Access application:**
```
http://localhost:5000
```

### Docker Configuration

**Environment Variables:**
```
SQUARE_ACCESS_TOKEN    - Your Square API token (required)
SECRET_KEY             - Session secret key (change for production)
FLASK_ENV              - Set to 'production' for deployment
DB_PATH                - Database file location (default: /app/data/schedules.db)
```

**Volumes:**
```
/app/data              - Database files (persistent)
/app/uploads           - Uploaded CSV files (persistent)
```

**Ports:**
```
5000                   - Web interface
```

### Health Check

The Docker container includes a health check. Verify:
```bash
docker ps
# STATUS should show "healthy"
```

---

## ⚙️ CONFIGURATION

### Initial Setup

#### 1. Change Default Password
```
1. Login with admin / admin123
2. Go to Admin → Users
3. Delete default admin user
4. Create new user with strong password
```

#### 2. Configure Square API Token
```
1. Go to Admin → Settings
2. Enter your Square API Access Token
3. Click "Test Connection"
4. Verify "API connection successful"
```

#### 3. Add Locations, Jobs, Team Members
```
Settings → Locations → Add
Settings → Jobs → Add
Settings → Team Members → Add
```

### Ongoing Maintenance

**Weekly:**
- Review upload history
- Check for any errors in processed schedules

**Monthly:**
- Archive old upload records (optional)
- Update team member list if staff changed

**As Needed:**
- Add new locations/jobs to lookup tables
- Update team member information

---

## 📝 CSV FORMAT

### Required Columns

| Column | Format | Example | Notes |
|--------|--------|---------|-------|
| employee_name | Text | John Smith | Name must match Team Member entry (optional for open shifts) |
| job_title | Text | Barista | Must match Job entry |
| location_name | Text | Arlington | Must match Location entry |
| shift_date | Date (YYYY-MM-DD) | 2025-05-23 | Future date |
| start_time | Time (HH:MM) | 09:00 | 24-hour format |
| end_time | Time (HH:MM) | 17:00 | Must be after start_time |
| timezone_offset | Text | -04:00 | EDT: -04:00, EST: -05:00, etc. |

### Example CSV (Complete)

```csv
employee_name,job_title,location_name,shift_date,start_time,end_time,timezone_offset
John Smith,Barista,Arlington,2025-05-23,09:00,17:00,-04:00
Jane Doe,Cashier,Arlington,2025-05-23,10:00,18:00,-04:00
Bob Johnson,Manager,Alexandria,2025-05-23,06:00,14:00,-04:00
(Open Shift),Barista,Arlington,2025-05-23,14:00,22:00,-04:00
Alice Brown,Barista,Arlington,2025-05-24,09:00,17:00,-04:00
```

### Tips

- **Empty employee_name** = Open shift (job only, no person assigned)
- **Timezone** = Use offset format (-04:00, not EDT)
- **Date** = Must be valid future date
- **Time** = 24-hour format (09:00, not 9:00 AM)

---

## 🚀 USAGE GUIDE

### Dashboard

**Summary Stats:**
- Total Locations configured
- Total Jobs configured
- Total Team Members
- Recent uploads
- Pending approvals

### Upload Workflow

#### 1. Upload CSV
```
Home → Upload Schedule
1. Select CSV file from computer
2. Click "Upload"
3. Wait for upload to complete
```

#### 2. Verify Data
```
Upload → Verify Preview
- Review each row
- Red rows = errors (see details)
- Green rows = ready to process
- Check for duplicates
- Confirm locations/jobs matched
```

#### 3. Detect Changes
```
Before approving:
- Click "Show Changes" to see what's different from last upload
- Added shifts highlighted in green
- Removed shifts highlighted in red
```

#### 4. Approve & Process
```
- Review summary
- Click "I approve - process these schedules"
- Monitor progress
- View results (success/error count)
```

#### 5. Review Results
```
History → Select upload
- See each row's result
- View shift IDs created in Square
- Download detailed report
```

### Settings Management

#### Manage Locations
```
Settings → Locations
1. View all locations
2. Add: Enter name + Square Location ID
3. Update: Edit existing entry
4. Delete: Remove from lookup (careful!)
```

**How to find Square Location ID:**
```
Square Dashboard → Settings → Locations
Click location name → Copy ID from URL
```

#### Manage Jobs
```
Settings → Jobs
Same as Locations, but for job titles
```

**How to find Square Job ID:**
```
Square Dashboard → Staff → Jobs
Click job name → Copy ID from URL
```

#### Manage Team Members
```
Settings → Team Members
Same as Locations, but for employees
```

**How to find Square Team Member ID:**
```
Square Dashboard → Staff → Team Members
Click employee name → Copy ID from URL
```

### Admin Functions

#### User Management
```
Admin → Users
1. View all users
2. Create new user: Username + Password (auto-hashed)
3. Delete user: Removes their account
```

#### View Settings
```
Admin → Settings
- Check Square API token (shows first 20 chars)
- Test API connection
```

#### Audit Trail
```
History
- View all uploads (up to 50)
- Click upload to see details
- See success/error counts
- Download results
```

---

## 🔍 CHANGE DETECTION

After uploading a CSV, you can see what changed:

**Added Shifts** (green)
- New shifts not in previous upload
- E.g., John scheduled extra shift on Friday

**Removed Shifts** (red)
- Shifts that were scheduled before but not in current upload
- E.g., Jane no longer scheduled Tuesday

**Modified Shifts**
- Shift details changed (time, location, job)

**How to Use:**
```
1. Upload new CSV
2. Click "Compare with Previous Upload"
3. Review changes before approving
4. Proceed with confidence
```

---

## 🐛 TROUBLESHOOTING

### "Cannot connect to Square API"

**Problem:** API connection fails

**Solutions:**
1. Verify SQUARE_ACCESS_TOKEN is set correctly
   ```bash
   # Windows: 
   echo %SQUARE_ACCESS_TOKEN%
   
   # Docker:
   docker-compose logs square-scheduler
   ```

2. Check token is valid
   - Go to Square Dashboard
   - Verify token hasn't expired
   - Create new token if needed

3. Check internet connection
   - Ping google.com
   - Verify firewall isn't blocking

### "Location not found" errors

**Problem:** CSV has location name that doesn't match database

**Solutions:**
1. Go to Settings → Locations
2. Add the location with correct Square Location ID
3. Ensure CSV uses exact same location name
4. Re-upload CSV

### "Job ID mismatch"

**Problem:** Job title in CSV doesn't match Settings

**Solutions:**
1. Check Settings → Jobs
2. Verify job exists
3. Check spelling/capitalization in CSV
4. Update CSV or Settings as needed

### Application won't start

**Problem:** Application crashes on startup

**Solutions:**
```bash
# Check for errors
python app.py

# Windows: Make sure Python 3.9+ is installed
python --version

# Docker: Check logs
docker-compose logs square-scheduler

# Delete database and restart (loses data!)
rm schedules.db
python app.py
```

### "Database locked"

**Problem:** SQLite database is locked (happens with multiple instances)

**Solutions:**
1. Don't run multiple instances of the app
2. Restart the application
3. Check for other Python processes running
4. In Docker: Restart the container
   ```bash
   docker-compose restart square-scheduler
   ```

### "CSRF token validation failed"

**Problem:** Session expired or invalid token

**Solutions:**
1. Logout and login again
2. Clear browser cookies
3. Hard refresh (Ctrl+F5)

---

## 📊 CSV VALIDATION

The application validates:
- ✓ All required columns present
- ✓ Location name exists in Settings
- ✓ Job title exists in Settings
- ✓ Employee name exists in Settings (or empty for open shifts)
- ✓ Shift date is valid and not past
- ✓ Start time before end time
- ✓ Timezone offset is valid format
- ✓ No duplicate shifts in same upload

---

## 🔐 SECURITY

### Authentication
- Simple username/password (hashed with SHA256)
- Session-based (login expires on browser close)
- Admin-only features protected

### Data
- Audit trail of all uploads
- User attribution (who uploaded what)
- No sensitive data stored

### Recommendations
1. Change default password immediately
2. Use strong passwords for all users
3. Regularly review audit trail
4. Keep Square token secure
5. Run in HTTPS in production (use reverse proxy)

---

## 💾 BACKUP & RECOVERY

### Database Backup

**Windows:**
```bash
# Manual backup
copy schedules.db schedules.db.backup

# Automated (Windows Task Scheduler)
# Create task to copy schedules.db daily
```

**Docker:**
```bash
# Backup database
docker cp square-schedule-manager:/app/data/schedules.db ./backup/

# Automated (cron job or similar)
```

### Restore from Backup
```bash
# Copy backup back to database location
copy schedules.db.backup schedules.db

# Restart application
```

---

## 📞 SUPPORT

### Common Issues

**Issue:** "File must be CSV"
- **Solution:** Make sure file extension is .csv (not .xlsx)

**Issue:** Large CSV takes too long
- **Solution:** Split into smaller files (limit to 500 rows per file)

**Issue:** Duplicate shift errors
- **Solution:** Check for duplicate rows in CSV

### Getting Help

1. Check Troubleshooting section above
2. Review application logs
3. Check Square API documentation (developer.squareup.com)

---

## 🎯 NEXT STEPS

1. **Download & Install** - Follow Quick Start section
2. **Gather Square IDs** - Get Location, Job, Team Member IDs
3. **Add Reference Data** - Populate Settings
4. **Create Sample CSV** - Test with a few shifts
5. **Upload & Verify** - Test the workflow
6. **Go Live** - Rollout to your team

---

## 📜 VERSION HISTORY

**v1.0.0** - Initial release
- CSV upload and processing
- Change detection
- User authentication
- Reference data management
- Audit trail

---

## ⚖️ LICENSE & TERMS

This application is provided as-is. Use at your own risk.

Always test with a sample CSV before processing production data.

---

## 🙏 ACKNOWLEDGMENTS

Built with:
- Python + Flask
- SQLite
- Square Labor API
- Modern HTML/CSS

---

**Happy scheduling! 📅**
