module.exports = {
  apps: [{
    name: "pdf-splitter",
    script: "./venv/bin/gunicorn",
    args: "--workers 3 --timeout 120 --bind 127.0.0.1:5000 app:app",
    cwd: "/var/www/pdf-split-s",
    interpreter: "none",
    autorestart: true,
    env: {
      FLASK_ENV: "production"
    }
  }]
}