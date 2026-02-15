from flask import Blueprint, request, jsonify, current_app, render_template, send_from_directory
from plugins.plugin_registry import get_plugin_instance
from utils.app_utils import resolve_path, handle_request_files, parse_form
from refresh_task import ManualRefresh, PlaylistRefresh
from dotenv import dotenv_values
import hashlib
import json
import os
import logging

logger = logging.getLogger(__name__)
plugin_bp = Blueprint("plugin", __name__)


def _get_env_path():
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(base_dir, '.env')


def _parse_env_file(filepath):
    if not os.path.exists(filepath):
        return []
    try:
        env_dict = dotenv_values(filepath)
        return list(env_dict.items())
    except Exception as e:
        logger.error(f"Error parsing .env file: {e}")
        return []


def _write_env_file(filepath, entries):
    try:
        with open(filepath, 'w') as f:
            f.write("# InkyPi API Keys and Secrets\n")
            f.write("# Managed via web interface\n\n")
            for key, value in entries:
                if value is None:
                    value = ""
                if ' ' in value or '"' in value or "'" in value:
                    value = f'"{value}"'
                f.write(f"{key}={value}\n")
        return True
    except Exception as e:
        logger.error(f"Error writing .env file: {e}")
        return False

def _delete_plugin_instance_images(device_config, plugin_instance_obj):
    """Delete all images associated with a plugin instance."""
    # Delete the plugin instance's generated image
    plugin_image_path = os.path.join(device_config.plugin_image_dir, plugin_instance_obj.get_image_path())
    if os.path.exists(plugin_image_path):
        try:
            os.remove(plugin_image_path)
            logger.info(f"Deleted plugin instance image: {plugin_image_path}")
        except Exception as e:
            logger.warning(f"Failed to delete plugin instance image {plugin_image_path}: {e}")

    # Call the plugin's cleanup method to handle plugin-specific resource cleanup
    try:
        plugin_config = device_config.get_plugin(plugin_instance_obj.plugin_id)
        if plugin_config:
            plugin = get_plugin_instance(plugin_config)
            plugin.cleanup(plugin_instance_obj.settings)
    except Exception as e:
        logger.warning(f"Error during plugin cleanup for {plugin_instance_obj.plugin_id}: {e}")

# Removed module-level PLUGINS_DIR - will resolve dynamically in route handlers

@plugin_bp.route('/plugin/<plugin_id>')
def plugin_page(plugin_id):
    device_config = current_app.config['DEVICE_CONFIG']
    playlist_manager = device_config.get_playlist_manager()

    # Find the plugin by id
    plugin_config = device_config.get_plugin(plugin_id)
    if plugin_config:
        try:
            plugin = get_plugin_instance(plugin_config)
            template_params = plugin.generate_settings_template()

            # retrieve plugin instance from the query parameters if updating existing plugin instance
            plugin_instance_name = request.args.get('instance')
            if plugin_instance_name:
                plugin_instance = playlist_manager.find_plugin(plugin_id, plugin_instance_name)
                if not plugin_instance:
                    return jsonify({"error": f"Plugin instance: {plugin_instance_name} does not exist"}), 500

                # add plugin instance settings to the template to prepopulate
                template_params["plugin_settings"] = plugin_instance.settings
                template_params["plugin_instance"] = plugin_instance_name
                template_params["refresh_settings"] = plugin_instance.refresh

            template_params["playlists"] = playlist_manager.get_playlist_names()
        except Exception as e:
            logger.exception("EXCEPTION CAUGHT: " + str(e))
            return jsonify({"error": f"An error occurred: {str(e)}"}), 500
        return render_template('plugin.html', plugin=plugin_config, **template_params)
    else:
        return "Plugin not found", 404

@plugin_bp.route('/images/<plugin_id>/<path:filename>')
def image(plugin_id, filename):
    # Resolve plugins directory dynamically
    plugins_dir = resolve_path("plugins")

    # Construct the full path to the plugin's file
    plugin_dir = os.path.join(plugins_dir, plugin_id)

    # Security check to prevent directory traversal
    safe_path = os.path.abspath(os.path.join(plugin_dir, filename))
    if not safe_path.startswith(os.path.abspath(plugins_dir)):
        return "Invalid path", 403

    # Convert to absolute path for send_from_directory
    abs_plugin_dir = os.path.abspath(plugin_dir)

    # Check if the directory and file exist
    if not os.path.isdir(abs_plugin_dir):
        logger.error(f"Plugin directory not found: {abs_plugin_dir}")
        return "Plugin directory not found", 404

    if not os.path.isfile(safe_path):
        logger.error(f"File not found: {safe_path}")
        return "File not found", 404

    # Serve the file from the plugin directory
    return send_from_directory(abs_plugin_dir, filename)

@plugin_bp.route('/plugin_instance_image/<path:playlist_name>/<path:plugin_id>/<path:instance_name>')
def plugin_instance_image(playlist_name, plugin_id, instance_name):
    """Serve the generated image for a plugin instance."""
    device_config = current_app.config['DEVICE_CONFIG']
    playlist_manager = device_config.get_playlist_manager()

    # Find the plugin instance
    playlist = playlist_manager.get_playlist(playlist_name)
    if not playlist:
        return "Playlist not found", 404

    plugin_instance = playlist.find_plugin(plugin_id, instance_name)
    if not plugin_instance:
        return "Plugin instance not found", 404

    # Get the image path
    image_filename = plugin_instance.get_image_path()
    image_path = os.path.join(device_config.plugin_image_dir, image_filename)

    # Check if the image exists
    if not os.path.exists(image_path):
        # Return a placeholder or 404
        return "Image not yet generated", 404

    # Serve the image
    return send_from_directory(device_config.plugin_image_dir, image_filename)

@plugin_bp.route('/delete_plugin_instance', methods=['POST'])
def delete_plugin_instance():
    device_config = current_app.config['DEVICE_CONFIG']
    playlist_manager = device_config.get_playlist_manager()

    data = request.json
    playlist_name = data.get("playlist_name")
    plugin_id = data.get("plugin_id")
    plugin_instance = data.get("plugin_instance")

    try:
        playlist = playlist_manager.get_playlist(playlist_name)
        if not playlist:
            return jsonify({"success": False, "message": "Playlist not found"}), 400

        # Get the plugin instance to find associated images
        plugin_instance_obj = playlist.find_plugin(plugin_id, plugin_instance)
        if not plugin_instance_obj:
            return jsonify({"success": False, "message": "Plugin instance not found"}), 400

        # Delete associated images before removing from playlist
        _delete_plugin_instance_images(device_config, plugin_instance_obj)

        result = playlist.delete_plugin(plugin_id, plugin_instance)
        if not result:
            return jsonify({"success": False, "message": "Plugin instance not found"}), 400

        # save changes to device config file
        device_config.write_config()

    except Exception as e:
        logger.exception("EXCEPTION CAUGHT: " + str(e))
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

    return jsonify({"success": True, "message": "Deleted plugin instance."})

@plugin_bp.route('/update_plugin_instance/<string:instance_name>', methods=['PUT'])
def update_plugin_instance(instance_name):
    device_config = current_app.config['DEVICE_CONFIG']
    playlist_manager = device_config.get_playlist_manager()

    try:
        form_data = parse_form(request.form)

        if not instance_name:
            raise RuntimeError("Instance name is required")

        plugin_id = form_data.pop("plugin_id")
        plugin_instance = playlist_manager.find_plugin(plugin_id, instance_name)
        if not plugin_instance:
            return jsonify({"error": f"Plugin instance: {instance_name} does not exist"}), 500

        # Handle refresh settings if provided
        refresh_settings_json = form_data.pop("refresh_settings", None)
        if refresh_settings_json:
            from utils.time_utils import calculate_seconds
            refresh_settings = json.loads(refresh_settings_json)
            refresh_type = refresh_settings.get('refreshType')

            if refresh_type == "interval":
                unit = refresh_settings.get('unit')
                interval = refresh_settings.get('interval')
                if unit and interval:
                    refresh_interval_seconds = calculate_seconds(int(interval), unit)
                    plugin_instance.refresh = {"interval": refresh_interval_seconds}
            elif refresh_type == "scheduled":
                refresh_time = refresh_settings.get('refreshTime')
                if refresh_time:
                    plugin_instance.refresh = {"scheduled": refresh_time}

        # Only update plugin settings if there's actual data (not just refresh settings)
        plugin_settings = form_data
        plugin_settings.update(handle_request_files(request.files, request.form))
        if plugin_id == "gpx_activities":
            # Never persist plain Garmin credentials in playlist config.
            plugin_settings.pop("garminEmail", None)
            plugin_settings.pop("garminPassword", None)

        if plugin_settings:  # Only update if there are actual plugin settings
            plugin_instance.settings = plugin_settings

        device_config.write_config()
    except Exception as e:
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500
    return jsonify({"success": True, "message": f"Updated plugin instance {instance_name}."})

@plugin_bp.route('/display_plugin_instance', methods=['POST'])
def display_plugin_instance():
    device_config = current_app.config['DEVICE_CONFIG']
    refresh_task = current_app.config['REFRESH_TASK']
    playlist_manager = device_config.get_playlist_manager()

    data = request.json
    playlist_name = data.get("playlist_name")
    plugin_id = data.get("plugin_id")
    plugin_instance_name = data.get("plugin_instance")

    try:
        playlist = playlist_manager.get_playlist(playlist_name)
        if not playlist:
            return jsonify({"success": False, "message": f"Playlist {playlist_name} not found"}), 400

        plugin_instance = playlist.find_plugin(plugin_id, plugin_instance_name)
        if not plugin_instance:
            return jsonify({"success": False, "message": f"Plugin instance '{plugin_instance_name}' not found"}), 400

        refresh_task.manual_update(PlaylistRefresh(playlist, plugin_instance, force=True))
    except Exception as e:
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

    return jsonify({"success": True, "message": "Display updated"}), 200

@plugin_bp.route('/update_now', methods=['POST'])
def update_now():
    device_config = current_app.config['DEVICE_CONFIG']
    refresh_task = current_app.config['REFRESH_TASK']
    display_manager = current_app.config['DISPLAY_MANAGER']

    try:
        plugin_settings = parse_form(request.form)
        plugin_settings.update(handle_request_files(request.files))
        plugin_id = plugin_settings.pop("plugin_id")
        if plugin_id == "gpx_activities":
            plugin_settings.pop("garminEmail", None)
            plugin_settings.pop("garminPassword", None)

        # Check if refresh task is running
        if refresh_task.running:
            refresh_task.manual_update(ManualRefresh(plugin_id, plugin_settings))
        else:
            # In development mode, directly update the display
            logger.info("Refresh task not running, updating display directly")
            plugin_config = device_config.get_plugin(plugin_id)
            if not plugin_config:
                return jsonify({"error": f"Plugin '{plugin_id}' not found"}), 404

            plugin = get_plugin_instance(plugin_config)
            image = plugin.generate_image(plugin_settings, device_config)
            display_manager.display_image(image, image_settings=plugin_config.get("image_settings", []))

    except Exception as e:
        logger.exception(f"Error in update_now: {str(e)}")
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

    return jsonify({"success": True, "message": "Display updated"}), 200


@plugin_bp.route('/plugin/gpx_activities/save_credentials', methods=['POST'])
def save_garmin_credentials():
    data = request.get_json() or {}

    email = (data.get("email") or "").strip()
    password = (data.get("password") or "").strip()
    current_email_key = (data.get("current_email_key") or "").strip()
    current_password_key = (data.get("current_password_key") or "").strip()

    if not email:
        return jsonify({"error": "Garmin email is required."}), 400
    if not password and not (current_email_key and current_password_key):
        return jsonify({"error": "Garmin password is required."}), 400

    email_hash = hashlib.sha1(email.lower().encode("utf-8")).hexdigest()[:10]
    email_key = f"GARMIN_EMAIL_{email_hash}"
    password_key = f"GARMIN_PASSWORD_{email_hash}"

    env_path = _get_env_path()
    env_entries = _parse_env_file(env_path)
    env_map = {k: v for k, v in env_entries if k}

    env_map[email_key] = email
    if password:
        env_map[password_key] = password
    elif current_password_key and current_password_key in env_map and current_password_key == password_key:
        password_key = current_password_key
    else:
        return jsonify({"error": "Garmin password is required."}), 400

    # Keep .env tidy by removing old keys for this plugin instance if they changed.
    old_keys = {current_email_key, current_password_key} - {email_key, password_key, ""}
    for key in old_keys:
        env_map.pop(key, None)

    ordered_entries = sorted(env_map.items(), key=lambda item: item[0])
    if not _write_env_file(env_path, ordered_entries):
        return jsonify({"error": "Failed to save Garmin credentials."}), 500

    os.environ[email_key] = env_map[email_key]
    os.environ[password_key] = env_map[password_key]

    return jsonify({
        "success": True,
        "email_key": email_key,
        "password_key": password_key
    }), 200
