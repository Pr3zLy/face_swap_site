from flask import Blueprint, render_template, request, redirect, url_for, session, flash, current_app
from werkzeug.security import check_password_hash, generate_password_hash
import functools
from file_helpers import load_config # Import load_config

admin_bp = Blueprint('admin', __name__)

# The config is loaded at the app level now, but for specific admin password check,
# we might reload it or ensure it's fresh if it could change during app lifetime (e.g. admin changes password)
# For simplicity, we assume it's loaded once at startup via current_app.
# If password can be changed live, this might need a direct load_config() in the login route.

def admin_required(view):
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if 'admin_logged_in' not in session:
            return redirect(url_for('admin.login', next=request.url))
        return view(**kwargs)
    return wrapped_view

@admin_bp.route('/')
@admin_required
def index():
    return "Admin Dashboard - Coming Soon" # Replace with a proper dashboard template

@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    if 'admin_logged_in' in session:
        return redirect(url_for('admin.index')) # Already logged in

    if request.method == 'POST':
        password = request.form.get('password')
        # Load current config to get the most up-to-date password hash
        # This is important if the password can be changed by the admin elsewhere.
        current_app_config = load_config()
        hashed_password = current_app_config.get('admin_password')

        if not hashed_password:
            flash('Admin password not configured. Please check server setup.', 'danger')
            # Log this error server-side as well
            current_app.logger.error("Admin password not found in configuration.")
            return render_template('admin/login.html')

        if password and check_password_hash(hashed_password, password):
            session['admin_logged_in'] = True
            session.permanent = True # Make session last longer, e.g. 30 days configured in app
            flash('Login successful!', 'success')
            next_url = request.args.get('next')
            return redirect(next_url or url_for('admin.index'))
        else:
            flash('Invalid credentials. Please try again.', 'danger')
    return render_template('admin/login.html')

@admin_bp.route('/logout')
@admin_required # Ensure only logged-in admin can access logout
def logout():
    session.pop('admin_logged_in', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('admin.login'))

import os # For file operations (exists, remove)
import uuid # For generating unique invite codes
from file_helpers import load_invites, save_invites # Import helpers for invites
from datetime import datetime, timezone # For timestamps

# Placeholder routes for other admin functionalities
@admin_bp.route('/invites', methods=['GET', 'POST'])
@admin_required
def manage_invites():
    if request.method == 'POST':
        invite_type = request.form.get('invite_type', 'image') # Default to 'image'
        if invite_type not in ['image', 'video']:
            flash('Invalid invite type specified.', 'danger')
        else:
            # Generate a unique invite code
            new_code = str(uuid.uuid4()) # Using UUID4 for simplicity and uniqueness

            invites = load_invites()
            # Check for collision, though highly unlikely with UUIDs
            while any(invite['code'] == new_code for invite in invites):
                new_code = str(uuid.uuid4())

            invites.append({
                "code": new_code,
                "type": invite_type, # 'image' or 'video'
                "used": False,
                "created_at": datetime.now(timezone.utc).isoformat()
            })
            if save_invites(invites):
                flash(f'New invite code generated: {new_code} (Type: {invite_type.capitalize()})', 'success')
            else:
                flash('Failed to save new invite code. Check server logs.', 'danger')
        return redirect(url_for('admin.manage_invites'))

    current_invites = load_invites()
    # Sort invites, e.g., by creation date (if available) or just as they are
    return render_template('admin/manage_invites.html', invites=current_invites)


from file_helpers import load_tasks, save_tasks, get_task_by_id, update_task # Added get_task_by_id, update_task
import shutil # For deleting directories (task uploads/outputs)

@admin_bp.route('/queue', methods=['GET', 'POST'])
@admin_required
def manage_queue():
    if request.method == 'POST':
        action = request.form.get('action')
        task_id = request.form.get('task_id')

        if not task_id:
            flash('Task ID is missing.', 'danger')
            return redirect(url_for('admin.manage_queue'))

        current_tasks = load_tasks()
        task_index = -1
        for i, t in enumerate(current_tasks):
            if t['task_id'] == task_id:
                task_index = i
                break

        if task_index == -1:
            flash(f'Task with ID {task_id} not found.', 'danger')
            return redirect(url_for('admin.manage_queue'))

        task_to_modify = current_tasks[task_index]

        if action == 'update_priority':
            try:
                new_priority = int(request.form.get('priority'))
                task_to_modify['priority'] = new_priority
                flash(f'Priority for task {task_id} updated to {new_priority}.', 'success')
            except (ValueError, TypeError):
                flash('Invalid priority value.', 'danger')

        elif action == 'retry_task':
            if task_to_modify['status'] == 'failed':
                task_to_modify['status'] = 'queued'
                task_to_modify.pop('error_message', None)
                task_to_modify.pop('stdout', None)
                task_to_modify.pop('stderr', None)
                task_to_modify.pop('started_at', None)
                task_to_modify.pop('completed_at', None)
                # Should also clean up output_path if it was partially created or from a previous failed attempt
                # For simplicity, we assume the worker will overwrite or handle this.
                # If an old output_path exists, it might be shown incorrectly if retry fails before worker clears it.
                task_to_modify['output_path'] = None
                flash(f'Task {task_id} has been re-queued.', 'success')
            else:
                flash(f'Task {task_id} cannot be retried as it is not in a "failed" state.', 'warning')

        elif action == 'delete_task':
            # Delete associated files/folders
            # Source/Target files are in uploads/<invite_code>/
            # Output file is in outputs/<invite_code>/
            invite_code = task_to_modify.get('invite_code')
            if invite_code:
                upload_dir_for_invite = os.path.join(current_app.config['UPLOADS_DIR'], invite_code)
                output_dir_for_invite = os.path.join(current_app.config['OUTPUTS_DIR'], invite_code)

                # Check if these are the *only* tasks for this invite code before deleting entire folder
                # For simplicity now, we assume one task per invite code for file deletion,
                # or that deleting the invite_code folder is acceptable if multiple tasks share it.
                # A safer approach would be to delete specific files if their names are unique per task.
                # Given current naming (source_uuid_name, target_uuid_name), files are unique.
                # Output is named <invite_code>.<ext>, so it's shared if multiple tasks use same invite.
                # Let's delete individual source/target files. Output dir might be shared.

                if task_to_modify.get('source_path') and os.path.exists(task_to_modify['source_path']):
                    try:
                        os.remove(task_to_modify['source_path'])
                        flash(f"Deleted source file for task {task_id}.", "info")
                    except OSError as e:
                        flash(f"Error deleting source file for task {task_id}: {e.strerror}", "danger")

                if task_to_modify.get('target_path') and os.path.exists(task_to_modify['target_path']):
                    try:
                        os.remove(task_to_modify['target_path'])
                        flash(f"Deleted target file for task {task_id}.", "info")
                    except OSError as e:
                        flash(f"Error deleting target file for task {task_id}: {e.strerror}", "danger")

                # For output, if the output_path is the invite_code dir, be very careful.
                # If output_path points to a specific file, delete that file.
                if task_to_modify.get('output_path') and os.path.exists(task_to_modify['output_path']):
                     if os.path.isfile(task_to_modify['output_path']):
                        try:
                            os.remove(task_to_modify['output_path'])
                            flash(f"Deleted output file for task {task_id}.", "info")
                        except OSError as e:
                            flash(f"Error deleting output file for task {task_id}: {e.strerror}", "danger")

                # Optionally, try to remove the invite_code subdirectories if they are empty
                # This is more complex and needs care if multiple tasks could share an invite_code
                # For now, leave the directories.

            current_tasks.pop(task_index)
            flash(f'Task {task_id} and associated files (if found) have been deleted.', 'success')

        else:
            flash('Invalid action specified.', 'danger')

        if not save_tasks(current_tasks):
            flash('Failed to save changes to tasks. Check server logs.', 'danger')

        return redirect(url_for('admin.manage_queue'))

    all_tasks = load_tasks()
    # Sort tasks for display: e.g., by status, then priority, then creation time
    def get_status_priority(status):
        order = {"processing": 0, "queued": 1, "completed": 2, "failed": 3}
        return order.get(status, 4)

    all_tasks.sort(key=lambda t: (
        get_status_priority(t.get('status', 'unknown')),
        t.get('priority', 99),
        t.get('created_at', '')
    ))
    return render_template('admin/manage_queue.html', tasks=all_tasks)


from file_helpers import save_config # Already have load_config

@admin_bp.route('/settings', methods=['GET', 'POST'])
@admin_required
def settings():
    if request.method == 'POST':
        old_password = request.form.get('old_password')
        new_password = request.form.get('new_password')
        confirm_new_password = request.form.get('confirm_new_password')

        if not old_password or not new_password or not confirm_new_password:
            flash('All password fields are required.', 'danger')
            return redirect(url_for('admin.settings'))

        if new_password != confirm_new_password:
            flash('New passwords do not match.', 'danger')
            return redirect(url_for('admin.settings'))

        if len(new_password) < 8: # Example: basic password policy
            flash('New password must be at least 8 characters long.', 'danger')
            return redirect(url_for('admin.settings'))

        config = load_config()
        current_hashed_password = config.get('admin_password')

        if not current_hashed_password or not check_password_hash(current_hashed_password, old_password):
            flash('Incorrect old password.', 'danger')
            return redirect(url_for('admin.settings'))

        # If all checks pass, hash the new password and save it
        config['admin_password'] = generate_password_hash(new_password)
        if save_config(config):
            flash('Admin password updated successfully. Please log in again if your session expires.', 'success')
            # Optionally, force re-login by clearing session:
            # session.pop('admin_logged_in', None)
            # return redirect(url_for('admin.login'))
        else:
            flash('Failed to save new password. Check server logs.', 'danger')

        return redirect(url_for('admin.settings'))

    return render_template('admin/settings.html')
