from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, session
import os
import uuid
from file_helpers import get_invite_by_code, load_tasks, save_tasks, update_invite_status # Import necessary helpers

user_bp = Blueprint('user', __name__)

# Removed get_invites_db and get_tasks_db as we now use file_helpers directly.


@user_bp.route('/', methods=['GET', 'POST'])
def enter_invite_code():
    if request.method == 'POST':
        code = request.form.get('invite_code', '').strip()
        if not code:
            flash('Please enter an invite code.', 'warning')
            return render_template('user/enter_invite.html') # Render to show message on same page

        invite = get_invite_by_code(code)

        if invite and not invite.get('used'):
            # Store invite details in session to pass to the render page
            # This is generally safer than passing sensitive info directly in URL if it were more complex
            # For just the code and type, URL is fine, but session is also a good practice.
            session['current_invite_code'] = invite['code']
            session['current_invite_type'] = invite['type']
            flash(f'Invite code accepted. You can proceed.', 'success')
            # Redirect to the page that will handle rendering, using the original code in the URL for clarity
            return redirect(url_for('user.render_page', invite_code=invite['code']))
        elif invite and invite.get('used'):
            flash('This invite code has already been used.', 'danger')
        else:
            flash('Invalid invite code. Please check and try again.', 'danger')

        return render_template('user/enter_invite.html') # Show error on the same page

    # For GET request, just show the form
    return render_template('user/enter_invite.html')


@user_bp.route('/render/<invite_code>', methods=['GET', 'POST'])
def render_page(invite_code):
    # Security check: Ensure the user arrived here legitimately via the invite code entry
    # and that the invite_code in URL matches what's in session.
    session_invite_code = session.get('current_invite_code')
    session_invite_type = session.get('current_invite_type')

    if not session_invite_code or session_invite_code != invite_code:
        flash('Invalid access to render page. Please enter your invite code again.', 'warning')
        return redirect(url_for('user.enter_invite_code'))

    # At this point, session_invite_code is valid and matches invite_code from URL.
    # We also have session_invite_type.

    # The actual invite object might be useful for display or further checks, though session type is primary driver
    # invite = get_invite_by_code(invite_code) # Already validated in previous step, but good for consistency
    # if not invite or invite.get('used'): # Double check, though previous step should handle 'used'
    #     flash('This invite code is no longer valid.', 'danger')
    #     session.pop('current_invite_code', None)
    #     session.pop('current_invite_type', None)
    #     return redirect(url_for('user.enter_invite_code'))


from werkzeug.utils import secure_filename
from datetime import datetime, timezone

ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'webm', 'mov'}
ALLOWED_TARGET_EXTENSIONS = ALLOWED_IMAGE_EXTENSIONS.union(ALLOWED_VIDEO_EXTENSIONS)

def allowed_file(filename, allowed_extensions):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in allowed_extensions

@user_bp.route('/render/<invite_code>', methods=['GET', 'POST'])
def render_page(invite_code):
    session_invite_code = session.get('current_invite_code')
    session_invite_type = session.get('current_invite_type')

    if not session_invite_code or session_invite_code != invite_code:
        flash('Invalid access to render page. Please enter your invite code again.', 'warning')
        return redirect(url_for('user.enter_invite_code'))

    if request.method == 'POST':
        source_file = request.files.get('source_image')
        target_file = request.files.get('target_media')

        if not source_file or source_file.filename == '':
            flash('Source image is required.', 'danger')
            return redirect(request.url)
        if not target_file or target_file.filename == '':
            flash('Target media is required.', 'danger')
            return redirect(request.url)

        if not allowed_file(source_file.filename, ALLOWED_IMAGE_EXTENSIONS):
            flash('Invalid source image file type. Allowed: png, jpg, jpeg, webp.', 'danger')
            return redirect(request.url)

        target_allowed_exts = ALLOWED_TARGET_EXTENSIONS if session_invite_type == 'video' else ALLOWED_IMAGE_EXTENSIONS
        if not allowed_file(target_file.filename, target_allowed_exts):
            flash(f'Invalid target file type for a "{session_invite_type}" invite. Allowed: {", ".join(target_allowed_exts)}', 'danger')
            return redirect(request.url)

        # Create unique folder for uploads based on invite_code
        # Using a subfolder within uploads/invite_code for original files
        upload_folder_for_invite = os.path.join(current_app.config['UPLOADS_DIR'], invite_code)
        os.makedirs(upload_folder_for_invite, exist_ok=True)

        source_filename = f"source_{uuid.uuid4().hex}_{secure_filename(source_file.filename)}"
        target_filename = f"target_{uuid.uuid4().hex}_{secure_filename(target_file.filename)}"

        source_path_rel = os.path.join(invite_code, source_filename) # Relative to UPLOADS_DIR
        target_path_rel = os.path.join(invite_code, target_filename) # Relative to UPLOADS_DIR

        source_path_abs = os.path.join(current_app.config['UPLOADS_DIR'], source_path_rel)
        target_path_abs = os.path.join(current_app.config['UPLOADS_DIR'], target_path_rel)

        try:
            source_file.save(source_path_abs)
            target_file.save(target_path_abs)
        except Exception as e:
            current_app.logger.error(f"Error saving uploaded files for invite {invite_code}: {e}")
            flash('An error occurred while saving your files. Please try again.', 'danger')
            return redirect(request.url)

        options = {
            'frame_processor_face_swapper': request.form.get('fp_face_swapper') == 'on',
            'frame_processor_face_enhancer': request.form.get('fp_face_enhancer') == 'on',
            'keep_fps': request.form.get('keep_fps') == 'on',
            'keep_audio': request.form.get('keep_audio') == 'on',
            'keep_frames': request.form.get('keep_frames') == 'on',
            'many_faces': request.form.get('many_faces') == 'on',
            'map_faces': request.form.get('map_faces') == 'on',
            'mouth_mask': request.form.get('mouth_mask') == 'on',
            'execution_provider_cuda': request.form.get('ep_cuda') == 'on',
            'execution_provider_cpu': request.form.get('ep_cpu') == 'on',
        }

        task_id = str(uuid.uuid4())

        # Determine task_type based on target file extension for more accurate priority setting
        # This overrides session_invite_type if e.g. an image is uploaded for a 'video' invite.
        target_ext = target_filename.rsplit('.', 1)[1].lower()
        actual_task_type = 'video' if target_ext in ALLOWED_VIDEO_EXTENSIONS else 'image'

        # Priority: Lower number is higher priority. Videos get higher priority.
        priority = 10 if actual_task_type == 'video' else 20

        new_task = {
            "task_id": task_id,
            "invite_code": invite_code,
            "source_path": source_path_abs, # Store absolute path for the worker
            "target_path": target_path_abs, # Store absolute path for the worker
            "options": options,
            "status": "queued",
            "output_path": None,
            "priority": priority,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "task_type": actual_task_type
        }

        tasks_data = load_tasks()
        tasks_data.append(new_task)
        if not save_tasks(tasks_data):
            flash('Failed to queue your task. Please try again or contact support.', 'danger')
            # Consider cleanup of uploaded files here if queueing fails
            return redirect(request.url)

        if not update_invite_status(invite_code, True):
            # This is a more critical error, means task is queued but invite status not updated
            current_app.logger.error(f"CRITICAL: Task {task_id} queued but failed to mark invite {invite_code} as used.")
            flash('Task queued, but there was an issue finalizing your invite. Please contact support if problems persist.', 'warning')
            # Don't delete files or task, as it's already in queue. Admin might need to check.

        # Clear the invite from session as it's now used
        session.pop('current_invite_code', None)
        session.pop('current_invite_type', None)

        flash('Your files have been uploaded and the task is now queued!', 'success')
        return redirect(url_for('user.task_status', task_id=task_id))

    return render_template('user/render_page.html', invite_code=invite_code, invite_type=session_invite_type)


from file_helpers import get_task_by_id # Import get_task_by_id
from flask import jsonify, send_from_directory

@user_bp.route('/status/<task_id>')
def task_status(task_id):
    task = get_task_by_id(task_id)
    if not task:
        flash('Task not found. It might have been an issue with submission or the ID is incorrect.', 'danger')
        return redirect(url_for('user.enter_invite_code'))

    # Make output_path relative for URL generation if it exists
    if task.get('output_path'):
        # Assuming output_path is stored as absolute. We need to make it relative to OUTPUTS_DIR
        # for constructing a URL that the /outputs_serve/ route can handle.
        try:
            # Example: C:\app\outputs\invite123\file.mp4 -> invite123/file.mp4
            task['display_output_path'] = os.path.relpath(task['output_path'], current_app.config['OUTPUTS_DIR'])
        except ValueError: # Handle cases where path might be on a different drive (Windows) or not relative
            task['display_output_path'] = None # Or log an error
            current_app.logger.error(f"Could not create relative path for task {task_id} output: {task['output_path']}")


    return render_template('user/task_status.html', task=task)

@user_bp.route('/api/task_status/<task_id>')
def api_task_status(task_id):
    task = get_task_by_id(task_id)
    if not task:
        return jsonify({"error": "Task not found", "status": "not_found"}), 404

    # Prepare a serializable version of the task, especially output_path
    api_task_data = task.copy()
    if api_task_data.get('output_path'):
        try:
            api_task_data['display_output_path'] = os.path.relpath(api_task_data['output_path'], current_app.config['OUTPUTS_DIR'])
        except ValueError:
            api_task_data['display_output_path'] = None

    # stdout and stderr can be very large, consider omitting or truncating for API response
    if 'stdout' in api_task_data:
        del api_task_data['stdout'] # Or truncate: api_task_data['stdout'] = api_task_data['stdout'][:1000]
    if 'stderr' in api_task_data:
        del api_task_data['stderr']

    return jsonify(api_task_data)

# Route to serve files from the OUTPUTS_DIR
# Important: Ensure this is secured if direct file access is a concern.
# For this project, it's assumed invite codes provide some level of obscurity.
@user_bp.route('/outputs_serve/<path:filepath>')
def serve_output_file(filepath):
    # filepath is expected to be relative to the OUTPUTS_DIR
    # e.g., invite_code/filename.mp4
    # Security: Be careful with send_from_directory and user-supplied paths.
    # os.path.normpath and secure_join can be useful.
    # Here, filepath comes from task['display_output_path'] which should be already safe.

    # Basic path traversal check (though relpath should handle most of it)
    if '..' in filepath or filepath.startswith('/'):
        current_app.logger.warning(f"Potential path traversal attempt: {filepath}")
        return "Invalid path", 400

    return send_from_directory(current_app.config['OUTPUTS_DIR'], filepath)
