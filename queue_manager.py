import os
import subprocess
import time
from threading import Thread
from file_helpers import load_config, load_tasks, update_task # Using centralized file helpers
from datetime import datetime # For sorting by creation date

# Define base directory for output files, can be made configurable if needed
# This assumes queue_manager.py is at the root of the project.
# If app.py sets current_app.config['OUTPUTS_DIR'], the worker would ideally get it from there.
# For a standalone worker, we might need a common config or pass it during initialization.
BASE_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'outputs')
os.makedirs(BASE_OUTPUT_DIR, exist_ok=True)


def process_task(task_details, app_config):
    """
    Processes a single task: activates venv and runs the run.py script.
    task_details: A dictionary representing the task from tasks.json.
    app_config: A dictionary with application configuration (e.g., path to Deep-Live-Cam).
    """
    task_id = task_details['task_id']
    print(f"[{datetime.now()}] Processing task: {task_id}")

    update_task(task_id, {"status": "processing", "started_at": datetime.now().isoformat()})

    deep_live_cam_base_path = app_config.get("deep_live_cam_path")
    if not deep_live_cam_base_path:
        print(f"ERROR: deep_live_cam_path not configured for task {task_id}.")
        update_task(task_id, {"status": "failed", "error_message": "Deep-Live-Cam path not configured."})
        return

    run_py_script_path = os.path.join(deep_live_cam_base_path, "run.py")

    # Determine python executable (venv or system)
    # This logic can be enhanced, e.g. check if venv path is valid
    venv_python_executable = os.path.join(deep_live_cam_base_path, "venv", "Scripts", "python.exe")
    if not os.path.exists(venv_python_executable): # Fallback or error
        print(f"WARNING: Venv python not found at {venv_python_executable} for task {task_id}. Trying system python.")
        # Potentially try global 'python' or specific version, or error out
        # For now, let's assume it must exist, or fail the task.
        update_task(task_id, {"status": "failed", "error_message": f"Venv Python not found at {venv_python_executable}"})
        return

    # Ensure the output directory for this specific invite code exists
    output_dir_for_task = os.path.join(BASE_OUTPUT_DIR, task_details['invite_code'])
    os.makedirs(output_dir_for_task, exist_ok=True)

    # Determine output filename and path
    # The task_type ('image' or 'video') should be reliable from when the task was created.
    file_extension = '.mp4' if task_details.get('task_type') == 'video' else '.jpg'
    output_filename = f"{task_details['invite_code']}{file_extension}" # Use invite_code as filename base
    output_file_path_abs = os.path.join(output_dir_for_task, output_filename)

    # The source_path and target_path in task_details are already absolute paths
    cmd = [
        venv_python_executable,
        run_py_script_path,
        '-s', task_details['source_path'], # This is absolute path
        '-t', task_details['target_path'], # This is absolute path
        '-o', output_file_path_abs
    ]

    options = task_details.get('options', {})
    frame_processors = []
    if options.get('frame_processor_face_swapper'):
        frame_processors.append('face_swapper')
    if options.get('frame_processor_face_enhancer'):
        frame_processors.append('face_enhancer')
    if frame_processors:
        cmd.extend(['--frame-processor'] + frame_processors)

    if options.get('keep_fps'): cmd.append('--keep-fps')
    if options.get('keep_audio'): cmd.append('--keep-audio') # Only relevant for video
    if options.get('keep_frames'): cmd.append('--keep-frames')
    if options.get('many_faces'): cmd.append('--many-faces')
    if options.get('map_faces'): cmd.append('--map-faces') # Not in provided list, but common
    if options.get('mouth_mask'): cmd.append('--mouth-mask') # Not in provided list, but common

    execution_providers = []
    if options.get('execution_provider_cuda'):
        execution_providers.append('cuda')
    if options.get('execution_provider_cpu'): # Assuming CPU is a fallback or specific choice
        execution_providers.append('cpu') # DeepFace usually defaults to CPU if CUDA not available/specified

    if execution_providers:
         cmd.extend(['--execution-provider'] + execution_providers)
    else: # Default to CPU if nothing is selected by user, or let run.py decide
        cmd.extend(['--execution-provider', 'cpu'])


    print(f"Executing command: {' '.join(cmd)}")
    print(f"[{datetime.now()}] Executing command for task {task_id}: {' '.join(cmd)}")
    try:
        process = subprocess.run(cmd, capture_output=True, text=True, check=True, cwd=deep_live_cam_base_path, timeout=1800) # Timeout 30 mins
        print(f"[{datetime.now()}] Task {task_id} completed successfully.")
        # print(f"Output for {task_id}: {process.stdout}") # Can be very verbose
        if process.stderr:
             print(f"[{datetime.now()}] Stderr for {task_id}: {process.stderr}")
        update_task(task_id, {"status": "completed", "output_path": output_file_path_abs, "completed_at": datetime.now().isoformat(), "stdout": process.stdout, "stderr": process.stderr})
    except subprocess.CalledProcessError as e:
        error_message = f"Return code: {e.returncode}"
        print(f"[{datetime.now()}] Error processing task {task_id}: {error_message}")
        print(f"Stdout for {task_id} (on error): {e.stdout}")
        print(f"Stderr for {task_id} (on error): {e.stderr}")
        update_task(task_id, {"status": "failed", "error_message": error_message, "stdout": e.stdout, "stderr": e.stderr, "completed_at": datetime.now().isoformat()})
    except subprocess.TimeoutExpired as e:
        error_message = "Processing timed out."
        print(f"[{datetime.now()}] Task {task_id} timed out.")
        print(f"Stdout for {task_id} (on timeout): {e.stdout}")
        print(f"Stderr for {task_id} (on timeout): {e.stderr}")
        update_task(task_id, {"status": "failed", "error_message": error_message, "stdout": e.stdout if e.stdout else "", "stderr": e.stderr if e.stderr else "", "completed_at": datetime.now().isoformat()})
    except Exception as e:
        error_message = f"An unexpected error occurred: {str(e)}"
        print(f"[{datetime.now()}] Unexpected error processing task {task_id}: {error_message}")
        update_task(task_id, {"status": "failed", "error_message": error_message, "completed_at": datetime.now().isoformat()})


def queue_worker():
    """
    The main worker loop that checks for queued tasks and processes them
    according to priority and creation time.
    """
    print(f"[{datetime.now()}] Queue worker started.")
    app_config = load_config() # Load main app configuration

    while True:
        all_tasks = load_tasks()
        queued_tasks = [task for task in all_tasks if task.get('status') == 'queued']

        if not queued_tasks:
            time.sleep(10) # Wait longer if no tasks
            continue

        # Sort tasks:
        # 1. By 'task_type': 'video' tasks come before 'image' tasks.
        #    (Achieved by assigning lower sort key to 'video')
        # 2. By 'priority': Lower explicit priority number means higher importance.
        # 3. By 'created_at': Older tasks of the same type and explicit priority come first (FIFO).
        def sort_key(task):
            task_type_priority = 0 if task.get('task_type') == 'video' else 1
            explicit_priority = task.get('priority', 99) # Default if not set
            created_time_str = task.get('created_at', datetime.min.isoformat())
            try:
                # Ensure created_at is a datetime object for proper comparison if needed,
                # though ISO format strings generally sort correctly too.
                # For simplicity, string comparison of ISO dates is usually fine.
                created_time = datetime.fromisoformat(created_time_str)
            except ValueError:
                created_time = datetime.min # Fallback for malformed dates

            # New sort order: Explicit Priority, then Task Type, then Creation Time
            return (explicit_priority, task_type_priority, created_time)

        queued_tasks.sort(key=sort_key)

        task_to_process = queued_tasks[0] # Get the highest priority task

        print(f"[{datetime.now()}] Selected task to process: {task_to_process['task_id']} (Priority: {task_to_process.get('priority')}, Type: {task_to_process.get('task_type')})")
        process_task(task_to_process, app_config)

        time.sleep(2) # Small delay before checking queue again


def start_worker_thread():
    """Starts the queue worker in a separate thread."""
    worker_thread = Thread(target=queue_worker, daemon=True)
    worker_thread.start()
    print("Queue worker thread initiated.")

if __name__ == '__main__':
    # This allows running the worker independently for testing
    print("Starting queue manager directly (for testing purposes).")
    # You might want to create a dummy tasks.json and config.json in ./data for this to run.
    if not os.path.exists(DATA_DIR_QM):
        os.makedirs(DATA_DIR_QM)
    if not os.path.exists(OUTPUTS_DIR_QM):
        os.makedirs(OUTPUTS_DIR_QM)

    # Example dummy task for testing when run directly:
    # Ensure you have a dummy config.json in data/
    # {
    #   "admin_password": "hashed_password",
    #   "deep_live_cam_path": "C:\\ai\\fake_webcam\\Deep-Live-Cam-2.1",
    #   "secret_key": "some_secret"
    # }
    # And a dummy tasks.json:
    # [
    #   {
    #     "task_id": "test-task-123",
    #     "invite_code": "testinvite",
    #     "source_path": "C:\\path\\to\\source.jpg", // Replace with actual paths
    #     "target_path": "C:\\path\\to\\target.jpg", // Replace with actual paths
    #     "options": {"frame_processor_face_swapper": true, "execution_provider_cpu": true},
    #     "status": "queued",
    #     "priority": 10,
    #     "task_type": "image" // or "video"
    #   }
    # ]
    # Note: For direct testing, ensure source/target paths are valid on your system.

    start_worker_thread()
    # Keep the main thread alive if running directly, otherwise it will exit.
    while True:
        time.sleep(60)
        print("Queue manager main thread still alive (if run directly)...")
