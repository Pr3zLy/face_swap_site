import json
import os
import fcntl # For file locking on POSIX systems
import msvcrt # For file locking on Windows
import time
import platform

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')
INVITES_FILE = os.path.join(DATA_DIR, 'invites.json')
TASKS_FILE = os.path.join(DATA_DIR, 'tasks.json')

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

# --- File Locking ---
# This provides basic cross-platform file locking.
# For more robust scenarios, especially with higher concurrency,
# a proper database or a more sophisticated locking mechanism might be needed.

def _lock_file(f):
    if platform.system() == "Windows":
        msvcrt.locking(f.fileno(), msvcrt.LK_RLCK, 1) # Lock 1 byte for testing
    else:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)

def _unlock_file(f):
    if platform.system() == "Windows":
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


# --- Generic Read/Write with Locking ---
def load_json_with_lock(file_path, default_data=None):
    if default_data is None:
        default_data = [] if 'tasks' in file_path or 'invites' in file_path else {}

    try:
        # Use 'r+' for reading and potentially creating if it doesn't exist,
        # but we'll handle creation more explicitly if needed.
        # For reading, 'r' is sufficient if we ensure files are pre-created.
        with open(file_path, 'r+') as f: # Open in r+ to allow locking
            _lock_file(f)
            try:
                # Check if file is empty
                f.seek(0, os.SEEK_END)
                if f.tell() == 0:
                    # File is empty, write default data and return it
                    f.seek(0)
                    json.dump(default_data, f, indent=4)
                    f.truncate() # Ensure no old data remains if file existed but was empty
                    data = default_data
                else:
                    # File is not empty, rewind and load
                    f.seek(0)
                    data = json.load(f)
            except json.JSONDecodeError:
                # File exists but is corrupted or not valid JSON.
                # Overwrite with default data.
                print(f"Warning: JSONDecodeError in {file_path}. Initializing with default.")
                f.seek(0) # Go to the beginning of the file
                f.truncate() # Clear the file content
                json.dump(default_data, f, indent=4)
                data = default_data
            finally:
                _unlock_file(f)
        return data
    except FileNotFoundError:
        # File doesn't exist, create it with default data
        print(f"Warning: File {file_path} not found. Creating with default data.")
        with open(file_path, 'w') as f:
            _lock_file(f) # Lock before writing
            json.dump(default_data, f, indent=4)
            _unlock_file(f) # Unlock after writing
        return default_data

def save_json_with_lock(file_path, data):
    try:
        with open(file_path, 'w') as f: # Open in 'w' to overwrite/create
            _lock_file(f)
            json.dump(data, f, indent=4)
            _unlock_file(f)
        return True
    except IOError as e:
        print(f"Error saving JSON to {file_path}: {e}")
        return False

# --- Config File Helpers ---
def load_config():
    return load_json_with_lock(CONFIG_FILE, {
        "admin_password": "pbkdf2:sha256:600000$VfOpL0Xr1g5g0kZm$c71df531654deaba5036787b00e428f57066801c98f7782dd588d60d4089f17b",  # Default for "admin"
        "deep_live_cam_path": "C:\\ai\\fake_webcam\\Deep-Live-Cam-2.1",
        "secret_key": "please_change_this_secret_key"
    })

def save_config(config_data):
    return save_json_with_lock(CONFIG_FILE, config_data)

# --- Invites File Helpers ---
def load_invites():
    return load_json_with_lock(INVITES_FILE, [])

def save_invites(invites_data):
    return save_json_with_lock(INVITES_FILE, invites_data)

def get_invite_by_code(invite_code):
    invites = load_invites()
    for invite in invites:
        if invite.get('code') == invite_code:
            return invite
    return None

def update_invite_status(invite_code, used_status):
    invites = load_invites()
    updated = False
    for invite in invites:
        if invite.get('code') == invite_code:
            invite['used'] = used_status
            updated = True
            break
    if updated:
        return save_invites(invites)
    return False

# --- Tasks File Helpers ---
def load_tasks():
    return load_json_with_lock(TASKS_FILE, [])

def save_tasks(tasks_data):
    return save_json_with_lock(TASKS_FILE, tasks_data)

def get_task_by_id(task_id):
    tasks = load_tasks()
    for task in tasks:
        if task.get('task_id') == task_id:
            return task
    return None

def update_task(task_id, updates):
    """
    Updates specific fields of a task.
    `updates` is a dictionary of fields to change.
    """
    tasks = load_tasks()
    task_found = False
    for task in tasks:
        if task.get('task_id') == task_id:
            task.update(updates)
            task_found = True
            break
    if task_found:
        return save_tasks(tasks)
    return False


if __name__ == '__main__':
    # Test functions
    print("Testing file_helpers.py")

    # Config
    print("\n--- Config Test ---")
    initial_config = load_config()
    print(f"Initial config: {initial_config}")
    initial_config['test_param'] = "test_value"
    if save_config(initial_config):
        print("Config saved.")
        loaded_conf = load_config()
        print(f"Loaded config: {loaded_conf}")
        assert loaded_conf.get('test_param') == "test_value"
        # Clean up test parameter
        del loaded_conf['test_param']
        save_config(loaded_conf)


    # Invites
    print("\n--- Invites Test ---")
    # Start with a clean slate for this test part if invites.json might have old test data
    save_invites([])
    initial_invites = load_invites()
    print(f"Initial invites: {initial_invites}")

    new_invite = {"code": "test12345", "type": "image", "used": False}
    initial_invites.append(new_invite)
    if save_invites(initial_invites):
        print("Invites saved.")
        loaded_invites = load_invites()
        print(f"Loaded invites: {loaded_invites}")
        assert len(loaded_invites) > 0

        retrieved_invite = get_invite_by_code("test12345")
        print(f"Retrieved invite 'test12345': {retrieved_invite}")
        assert retrieved_invite is not None and not retrieved_invite['used']

        if update_invite_status("test12345", True):
            print("Invite status updated.")
            updated_invite = get_invite_by_code("test12345")
            print(f"Updated invite 'test12345': {updated_invite}")
            assert updated_invite and updated_invite['used']

    # Tasks
    print("\n--- Tasks Test ---")
    save_tasks([]) # Clean slate
    initial_tasks = load_tasks()
    print(f"Initial tasks: {initial_tasks}")

    new_task = {"task_id": "task-abcde", "status": "queued", "data": "dummy"}
    initial_tasks.append(new_task)
    if save_tasks(initial_tasks):
        print("Tasks saved.")
        loaded_tasks = load_tasks()
        print(f"Loaded tasks: {loaded_tasks}")
        assert len(loaded_tasks) > 0

        retrieved_task = get_task_by_id("task-abcde")
        print(f"Retrieved task 'task-abcde': {retrieved_task}")
        assert retrieved_task is not None and retrieved_task['status'] == 'queued'

        if update_task("task-abcde", {"status": "processing", "progress": "50%"}):
            print("Task updated.")
            updated_task_info = get_task_by_id("task-abcde")
            print(f"Updated task 'task-abcde': {updated_task_info}")
            assert updated_task_info and updated_task_info['status'] == 'processing' and updated_task_info['progress'] == '50%'

    print("\nFile helper tests complete.")
    # Clean up by removing test files or resetting them
    # For simplicity, we'll just re-save them empty or with defaults
    save_config(load_json_with_lock(CONFIG_FILE, default_data={
        "admin_password": "pbkdf2:sha256:600000$VfOpL0Xr1g5g0kZm$c71df531654deaba5036787b00e428f57066801c98f7782dd588d60d4089f17b",
        "deep_live_cam_path": "C:\\ai\\fake_webcam\\Deep-Live-Cam-2.1",
        "secret_key": "please_change_this_secret_key"
    }))
    save_invites([])
    save_tasks([])
    print("Test data cleaned up.")
