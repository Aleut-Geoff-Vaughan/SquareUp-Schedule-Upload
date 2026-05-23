# Square Schedule Manager - Project Structure & Deployment

## 📁 PROJECT STRUCTURE

```
square-scheduler/
├── app.py                          # Main Flask application
├── database.py                     # Database management
├── square_api.py                   # Square API integration
├── requirements.txt                # Python dependencies
├── Dockerfile                      # Docker container definition
├── docker-compose.yml              # Docker Compose orchestration
├── README.md                       # Complete documentation
├── .env                           # Environment variables (create this)
│
├── templates/                      # HTML templates (create folder)
│   ├── base.html                  # Base layout template
│   ├── login.html                 # Login page
│   ├── dashboard.html             # Main dashboard
│   ├── upload.html                # CSV upload page
│   ├── settings_locations.html    # Manage locations
│   ├── settings_jobs.html         # Manage jobs
│   ├── settings_team_members.html # Manage team members
│   ├── admin_users.html           # User management
│   ├── history.html               # Upload history
│   ├── 404.html                   # Error page
│   └── 500.html                   # Server error page
│
├── static/                         # Static files (create folder)
│   ├── css/
│   │   └── style.css              # Main stylesheet
│   └── js/
│       └── app.js                 # Frontend JavaScript
│
├── data/                          # Data directory (created at runtime)
│   └── schedules.db               # SQLite database (created at runtime)
│
└── uploads/                       # Uploaded CSV files (created at runtime)
```

---

## 🚀 QUICK DEPLOYMENT GUIDE

### For Windows Users (Direct Python)

#### Step 1: Download & Extract
```
1. Download all application files
2. Extract to C:\SquareScheduleManager
3. Open PowerShell in that folder
```

#### Step 2: Install Python (if needed)
```
1. Download Python 3.11 from python.org
2. Install with "Add Python to PATH" checked
3. Verify: python --version
```

#### Step 3: Install Dependencies
```powershell
# In PowerShell
pip install -r requirements.txt
```

#### Step 4: Create .env File
```powershell
# Create .env file with your Square token
@"
SQUARE_ACCESS_TOKEN=YOUR_SQUARE_TOKEN_HERE
SECRET_KEY=your-secret-key-change-in-production
"@ | Out-File -Encoding UTF8 .env
```

#### Step 5: Run Application
```powershell
# Option A: Direct run
python app.py

# Option B: Create batch file (run-app.bat)
@echo off
python app.py
pause
```

#### Step 6: Access Application
```
Open browser: http://localhost:5000
Username: admin
Password: admin123
```

---

### For Docker Users (Windows or Linux)

#### Step 1: Install Docker Desktop
```
1. Download Docker Desktop from docker.com
2. Install and restart computer
3. Verify: docker --version
```

#### Step 2: Create .env File
```
Create file called .env in your project folder:

SQUARE_ACCESS_TOKEN=YOUR_SQUARE_TOKEN_HERE
SECRET_KEY=your-secret-key-change-in-production
```

#### Step 3: Run with Docker Compose
```bash
# Navigate to project folder
cd square-scheduler

# Build and run
docker-compose up -d

# View logs
docker-compose logs -f

# Stop application
docker-compose down
```

#### Step 4: Access Application
```
Open browser: http://localhost:5000
Username: admin
Password: admin123
```

---

## 📋 FOLDER STRUCTURE: Creating Required Directories

### Templates Folder (HTML Pages)

Create `templates/base.html`:
```html
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Square Schedule Manager</title>
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
    <style>
        body { font-family: 'Segoe UI', sans-serif; margin: 0; padding: 0; background: #f5f5f5; }
        .navbar { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 15px 30px; display: flex; justify-content: space-between; align-items: center; }
        .navbar h1 { margin: 0; font-size: 24px; }
        .nav-links { display: flex; gap: 20px; }
        .nav-links a { color: white; text-decoration: none; padding: 8px 12px; border-radius: 4px; transition: background 0.3s; }
        .nav-links a:hover { background: rgba(255,255,255,0.1); }
        .nav-links a.active { background: rgba(255,255,255,0.2); }
        .container { max-width: 1200px; margin: 30px auto; padding: 0 20px; }
        .card { background: white; border-radius: 8px; padding: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); margin-bottom: 20px; }
        .btn { display: inline-block; padding: 10px 20px; background: #667eea; color: white; text-decoration: none; border: none; border-radius: 4px; cursor: pointer; font-weight: 600; transition: transform 0.2s; }
        .btn:hover { transform: translateY(-2px); }
        .btn.danger { background: #e74c3c; }
        .alert { padding: 15px; border-radius: 4px; margin-bottom: 20px; }
        .alert.success { background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .alert.error { background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        table th { background: #f8f9fa; padding: 12px; text-align: left; font-weight: 600; border-bottom: 2px solid #dee2e6; }
        table td { padding: 12px; border-bottom: 1px solid #dee2e6; }
        table tr:hover { background: #f8f9fa; }
        .footer { text-align: center; padding: 20px; color: #666; font-size: 12px; margin-top: 40px; }
    </style>
</head>
<body>
    <div class="navbar">
        <h1>📅 Square Schedule Manager</h1>
        <div class="nav-links">
            <a href="/dashboard">Dashboard</a>
            <a href="/upload">Upload</a>
            <a href="/settings/locations">Settings</a>
            <a href="/history">History</a>
            {% if session.is_admin %}
            <a href="/admin/users">Admin</a>
            {% endif %}
            <a href="/logout">Logout ({{ session.username }})</a>
        </div>
    </div>
    
    <div class="container">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                <div class="alert alert-{{ category }}">{{ message }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        
        {% block content %}{% endblock %}
    </div>
    
    <div class="footer">
        <p>Square Schedule Manager v1.0.0 | Built with Python, Flask, and ❤️</p>
    </div>
    
    <script src="{{ url_for('static', filename='js/app.js') }}"></script>
    {% block scripts %}{% endblock %}
</body>
</html>
```

Create `templates/dashboard.html`:
```html
{% extends "base.html" %}

{% block content %}
<h2>Dashboard</h2>

<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 30px;">
    <div class="card">
        <h3>📍 Locations</h3>
        <p style="font-size: 24px; font-weight: bold; color: #667eea;">{{ stats.total_locations }}</p>
        <a href="/settings/locations" class="btn">Manage</a>
    </div>
    
    <div class="card">
        <h3>💼 Jobs</h3>
        <p style="font-size: 24px; font-weight: bold; color: #667eea;">{{ stats.total_jobs }}</p>
        <a href="/settings/jobs" class="btn">Manage</a>
    </div>
    
    <div class="card">
        <h3>👥 Team Members</h3>
        <p style="font-size: 24px; font-weight: bold; color: #667eea;">{{ stats.total_team_members }}</p>
        <a href="/settings/team-members" class="btn">Manage</a>
    </div>
    
    <div class="card">
        <h3>📤 Recent Uploads</h3>
        <p style="font-size: 24px; font-weight: bold; color: #667eea;">{{ stats.recent_uploads }}</p>
        <a href="/history" class="btn">View</a>
    </div>
    
    <div class="card">
        <h3>⏳ Pending Approvals</h3>
        <p style="font-size: 24px; font-weight: bold; color: #e74c3c;">{{ stats.pending_approvals }}</p>
        <a href="/upload" class="btn">Process</a>
    </div>
</div>

<div class="card">
    <h3>🚀 Quick Start</h3>
    <ol>
        <li><a href="/settings/locations">Set up your Locations</a></li>
        <li><a href="/settings/jobs">Add your Jobs</a></li>
        <li><a href="/settings/team-members">Add your Team Members</a></li>
        <li><a href="/upload">Upload your first CSV schedule</a></li>
    </ol>
</div>
{% endblock %}
```

Create `templates/upload.html`:
```html
{% extends "base.html" %}

{% block content %}
<h2>Upload Schedule</h2>

<div class="card">
    <h3>📤 Step 1: Select CSV File</h3>
    <form id="uploadForm" enctype="multipart/form-data">
        <div style="margin: 20px 0;">
            <label>Choose CSV file:</label><br>
            <input type="file" id="csvFile" accept=".csv" required style="padding: 10px; margin: 10px 0;">
            <button type="submit" class="btn">Upload</button>
        </div>
    </form>
</div>

<div id="uploadStatus"></div>
<div id="previewSection"></div>

<script>
document.getElementById('uploadForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const formData = new FormData();
    formData.append('file', document.getElementById('csvFile').files[0]);
    
    const response = await fetch('/upload', { method: 'POST', body: formData });
    const data = await response.json();
    
    if (data.success) {
        document.getElementById('uploadStatus').innerHTML = `
            <div class="card alert alert-success">
                <strong>✅ Upload Successful!</strong><br>
                ${data.row_count} rows ready for verification
            </div>
        `;
        loadPreview();
    } else {
        document.getElementById('uploadStatus').innerHTML = `
            <div class="card alert alert-error">
                <strong>❌ Error:</strong> ${data.error}
            </div>
        `;
    }
});

async function loadPreview() {
    const response = await fetch('/api/verify-preview');
    const data = await response.json();
    
    let html = '<div class="card"><h3>📋 Step 2: Verify Data</h3>';
    html += `<p><strong>Total Rows:</strong> ${data.total_rows} | <strong style="color:green">Valid:</strong> ${data.valid_rows} | <strong style="color:red">Invalid:</strong> ${data.invalid_rows}</p>`;
    html += '<table style="font-size: 12px;"><thead><tr><th>Row</th><th>Employee</th><th>Job</th><th>Location</th><th>Date</th><th>Time</th><th>Status</th></tr></thead><tbody>';
    
    data.rows.forEach(row => {
        const status = row.is_valid ? '✅' : `❌ ${row.errors.join(', ')}`;
        html += `<tr><td>${row.row_number}</td><td>${row.employee_name}</td><td>${row.job_title}</td><td>${row.location_name}</td><td>${row.shift_date}</td><td>${row.start_time}-${row.end_time}</td><td>${status}</td></tr>`;
    });
    
    html += '</tbody></table>';
    html += '<div style="margin-top: 20px;"><button class="btn" onclick="approveProcess()">Approve & Process</button></div>';
    html += '</div>';
    
    document.getElementById('previewSection').innerHTML = html;
}

async function approveProcess() {
    const response = await fetch('/api/process-schedules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approve: true })
    });
    const data = await response.json();
    
    let html = `<div class="card alert alert-success">
        <h3>✅ Processing Complete!</h3>
        <p><strong>${data.success_count}</strong> schedules created</p>
        <p style="color: red;"><strong>${data.error_count}</strong> errors</p>
    </div>`;
    
    document.getElementById('previewSection').innerHTML = html;
}
</script>
{% endblock %}
```

---

## 🔧 INITIAL SETUP CHECKLIST

After deploying the application:

- [ ] Application starts without errors
- [ ] Can login with admin/admin123
- [ ] Can access dashboard
- [ ] Change admin password
- [ ] Add at least one Location
- [ ] Add at least one Job
- [ ] Add at least one Team Member
- [ ] Configure Square API token
- [ ] Test with sample CSV
- [ ] Verify shift appears in Square Dashboard

---

## 🐳 DOCKER SPECIFICS

### Building Custom Image
```bash
docker build -t my-square-scheduler:1.0 .
```

### Running Standalone (without docker-compose)
```bash
docker run -d \
  --name square-scheduler \
  -p 5000:5000 \
  -e SQUARE_ACCESS_TOKEN=your_token \
  -e SECRET_KEY=your_secret \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/uploads:/app/uploads \
  square-scheduler:1.0
```

### Production Docker Compose with PostgreSQL
```yaml
version: '3.8'
services:
  web:
    build: .
    ports:
      - "5000:5000"
    environment:
      SQUARE_ACCESS_TOKEN: ${SQUARE_ACCESS_TOKEN}
      DATABASE_URL: postgresql://user:password@db/square_scheduler
    depends_on:
      - db
  
  db:
    image: postgres:14
    environment:
      POSTGRES_PASSWORD: password
      POSTGRES_DB: square_scheduler
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
```

---

## 🔐 PRODUCTION CONSIDERATIONS

1. **HTTPS**: Use reverse proxy (nginx) with SSL certificates
2. **Environment Variables**: Store all secrets in .env, never commit to git
3. **Database**: Use PostgreSQL instead of SQLite for production
4. **Backups**: Implement daily backup strategy
5. **Monitoring**: Set up application monitoring and alerts
6. **Scaling**: Use Kubernetes or container orchestration for high availability

---

## 📞 SUPPORT CHECKLIST

Before asking for help, verify:
- [ ] All files are in correct folders (templates/, static/)
- [ ] Python 3.9+ installed (or Docker installed)
- [ ] SQUARE_ACCESS_TOKEN is set
- [ ] Database file has proper permissions
- [ ] Port 5000 is not in use by another application
- [ ] No firewall blocking localhost:5000

---

**Ready to deploy! 🚀**
