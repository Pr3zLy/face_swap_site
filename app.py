import os
from flask import Flask
from werkzeug.security import generate_password_hash

# Import blueprints
# from admin_routes import admin_bp
# from user_routes import user_bp

import secrets
from file_helpers import load_config, save_config # Import helpers

# Import blueprints
from admin_routes import admin_bp
from user_routes import user_bp

# Define the path for the data directory, uploads, and outputs
# These are relative to the app.py file location
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
UPLOADS_DIR = os.path.join(BASE_DIR, 'uploads')
OUTPUTS_DIR = os.path.join(BASE_DIR, 'outputs')

# Ensure data, uploads and outputs directories exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(OUTPUTS_DIR, exist_ok=True)

# --- Configuration Loading ---
app_config = load_config()

# Ensure a secret key is set, generate one if not present in config.json
if 'secret_key' not in app_config or app_config['secret_key'] == 'please_change_this_secret_key':
    print("Generating a new secret key and saving to config.json")
    app_config['secret_key'] = secrets.token_hex(32)
    save_config(app_config)


app = Flask(__name__)
app.config['SECRET_KEY'] = app_config['secret_key']
app.config['DATA_DIR'] = DATA_DIR
app.config['UPLOADS_DIR'] = UPLOADS_DIR
app.config['OUTPUTS_DIR'] = OUTPUTS_DIR
app.config['DEEP_LIVE_CAM_PATH'] = app_config.get('deep_live_cam_path', "C:\\ai\\fake_webcam\\Deep-Live-Cam-2.1") # Fallback just in case

# Register blueprints
app.register_blueprint(admin_bp, url_prefix='/admin')
app.register_blueprint(user_bp)

@app.route('/health')
def health_check():
    return "OK", 200

if __name__ == '__main__':
    # Create a default config if it doesn't exist
    # This is also a good place to initialize the admin password if not set
    # For now, we assume config.json is pre-populated as per previous step.

    # Start the queue worker thread
    from queue_manager import start_worker_thread
    start_worker_thread()

    app.run(debug=True) # debug=False for production, typically. Use_reloader=False if debug=True causes worker to start twice.
    # When using Flask's reloader (debug=True by default), be aware that it might start the worker thread twice.
    # For development, this might be acceptable, or use app.run(debug=True, use_reloader=False)
    # For production, a proper WSGI server (like Gunicorn or Waitress) would be used, and the worker
    # might be run as a separate process or managed differently.
