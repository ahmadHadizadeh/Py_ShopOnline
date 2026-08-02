import os
import re
import json
from pathlib import Path
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

# --- Configuration ---
PROJECT_ROOT = Path(
    __file__
).parent  # Assumes script is in project root alongside manage.py
OUTPUT_DIR = PROJECT_ROOT / "ai_extracted_data"
# !!! IMPORTANT: Replace 'your_project_name' with the actual name of your project's main settings directory (e.g., 'config', 'core', 'myproject')
# You might need to manually find this if it's not obvious, e.g., by looking at INSTALLED_APPS in your current settings.py
SETTINGS_FILE_NAME = "settings.py"  # Default settings file name
PROJECT_SETTINGS_DIR_NAME = None  # Will be auto-detected or needs manual setting

# --- Helper Functions ---


def clean_code_string(code_str):
    """Removes common prefixes/suffixes and extra whitespace from code strings."""
    if not isinstance(code_str, str):
        return str(code_str)
    # Remove quotes
    code_str = code_str.strip().strip("'\"")
    # Normalize whitespace
    code_str = re.sub(r"\s+", " ", code_str)
    return code_str


def extract_app_name_from_path(file_path: Path, project_root: Path) -> str | None:
    """Tries to determine the app name from the file path relative to project root."""
    try:
        relative_path = file_path.relative_to(project_root)
        # Direct app structure: project_root/app_name/models.py
        if (
            len(relative_path.parts) >= 2 and relative_path.parts[0] == file_path.name
        ):  # This logic seems off, let's simplify.
            # Check if the parent directory of models.py is a top-level directory in project_root
            app_dir = file_path.parent
            if app_dir.parent == project_root:
                return app_dir.name
        # Heuristic based on common app directory names directly under project root
        # This will be refined by find_project_apps based on INSTALLED_APPS
        if len(relative_path.parts) >= 1:
            # Return the first part if it seems like a plausible app name directory
            # This is a fallback, primary identification happens via INSTALLED_APPS
            return relative_path.parts[0]

    except ValueError:
        pass
    return None


def parse_settings_value(value_str, settings_file_path):
    """Parses various types of values found in Django settings."""
    value_str = value_str.strip()
    # Handle strings
    if (value_str.startswith("'") and value_str.endswith("'")) or (
        value_str.startswith('"') and value_str.endswith('"')
    ):
        return value_str[1:-1]
    # Handle lists
    if value_str.startswith("[") and value_str.endswith("]"):
        try:
            # Use json.loads for robust parsing of lists, tuples, etc.
            return json.loads(value_str.replace("'", '"'))
        except json.JSONDecodeError:
            # Fallback for potentially complex list items not easily JSON-serializable
            items = [
                clean_code_string(item)
                for item in value_str[1:-1].split(",")
                if item.strip()
            ]
            return items
    # Handle True/False/None
    if value_str in ("True", "False", "None"):
        return value_str
    # Handle numbers
    try:
        return int(value_str)
    except ValueError:
        try:
            return float(value_str)
        except ValueError:
            pass
    # Handle references to other settings or models (e.g., 'settings.AUTH_USER_MODEL')
    if "." in value_str:
        # Try to resolve common settings references
        if value_str.startswith("settings."):
            setting_name = value_str.split(".")[1]
            # Simplistic lookup: we'll just return the string representation
            # A more robust solution would involve actually importing and evaluating settings.py
            return f"'{setting_name}' (resolved from settings)"
        # Handle 'gettext_lazy' or similar lazy translations
        if "gettext_lazy(" in value_str:
            match = re.search(r"gettext_lazy\(['\"](.*?)['\"]\)", value_str)
            if match:
                return match.group(1)
        # Fallback for other dotted names
        return clean_code_string(value_str)
    # Default: return as string
    return clean_code_string(value_str)


# --- Extraction Functions ---


def extract_models(apps_dirs):
    """Scans for models.py files and extracts class definitions."""
    extracted_models = {}
    model_files = []
    for app_dir in apps_dirs:
        # Look for models.py directly or models/__init__.py
        models_file_primary = app_dir / "models.py"
        models_file_secondary = app_dir / "models" / "__init__.py"

        if models_file_primary.is_file():
            model_files.append(models_file_primary)
        elif models_file_secondary.is_file():
            model_files.append(models_file_secondary)

    for model_file_path in model_files:
        app_name = extract_app_name_from_path(model_file_path, PROJECT_ROOT)
        if not app_name:
            logging.warning(f"Could not determine app name for: {model_file_path}")
            continue

        logging.info(f"Scanning models in: {model_file_path}")
        try:
            with open(model_file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Regex to find Django Model classes (simplistic: class ModelName(models.Model):)
            # It captures the class name and the parent class(es)
            model_pattern = re.compile(
                r"class\s+(\w+)\s*\(\s*(?:models\.|BaseModel\.|)Model(?:,\s*\w+)*\s*\):",
                re.MULTILINE,
            )
            matches = model_pattern.finditer(content)

            models_in_file = []
            for match in matches:
                model_name = match.group(1)
                # Basic check to avoid abstract base classes or other constructs that might match
                # We assume anything matching this pattern is a concrete model for now
                models_in_file.append(model_name)
                logging.debug(f"  Found model: {model_name}")

            if models_in_file:
                if app_name not in extracted_models:
                    extracted_models[app_name] = []
                extracted_models[app_name].extend(models_in_file)

        except Exception as e:
            logging.error(f"Error reading model file {model_file_path}: {e}")

    # Remove duplicates and sort
    for app in extracted_models:
        extracted_models[app] = sorted(list(set(extracted_models[app])))

    return extracted_models


def find_project_settings_dir(project_root: Path) -> Path | None:
    """Attempts to find the directory containing settings.py."""
    settings_file_name = "settings.py"
    # Try common names for the project's main configuration directory
    common_settings_dirs = [
        "config",
        "core",
        "myproject",
        "project",
        "settings",
    ]  # Add any other common names

    # First, check if settings.py is directly in the project root (less common for Django projects)
    if (project_root / settings_file_name).is_file():
        return project_root

    # Then, check common subdirectories
    for dir_name in common_settings_dirs:
        settings_dir = project_root / dir_name
        if settings_dir.is_dir() and (settings_dir / settings_file_name).is_file():
            return settings_dir

    # Fallback: Check directories that are NOT apps, tests, venv, etc.
    # This is more aggressive and might pick a wrong directory if structure is unusual.
    potential_dirs = [
        d
        for d in project_root.iterdir()
        if d.is_dir()
        and not d.name.startswith(
            (".", "_", "venv", "tests", "docs", "static", "media", "apps", "src")
        )
    ]
    for potential_dir in potential_dirs:
        if (potential_dir / settings_file_name).is_file():
            return potential_dir

    logging.warning(
        f"Could not automatically find the directory containing {settings_file_name}."
    )
    return None


def extract_settings_data(settings_path):
    """Extracts specific settings like INSTALLED_APPS, AUTH_USER_MODEL, DATABASES."""
    extracted_settings = {
        "INSTALLED_APPS": [],
        "AUTH_USER_MODEL": None,
        "DATABASES": {},
        "MIDDLEWARE": [],
        "STATIC_URL": None,
        "MEDIA_URL": None,
        "ROOT_URLCONF": None,
        "WSGI_APPLICATION": None,
        "ASGI_APPLICATION": None,
        # Add other settings you deem critical for logic here
    }

    if not settings_path or not settings_path.is_file():
        logging.warning(
            f"Settings file not found at: {settings_path}. Skipping settings extraction."
        )
        return extracted_settings

    logging.info(f"Scanning settings file: {settings_path}")
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Regex to find key assignments (simplistic)
        # Handles: KEY = value, KEY = [ ... ], KEY = { ... }
        setting_pattern = re.compile(r"(\w+)\s*=\s*(.+)", re.MULTILINE)

        for match in setting_pattern.finditer(content):
            key = match.group(1)
            value_str = match.group(2)

            if key in extracted_settings:
                try:
                    parsed_value = parse_settings_value(value_str, settings_path)
                    # Special handling for INSTALLED_APPS which can be a list of strings
                    if key == "INSTALLED_APPS":
                        if isinstance(parsed_value, list):
                            extracted_settings[key].extend(
                                [clean_code_string(item) for item in parsed_value]
                            )
                        elif (
                            isinstance(parsed_value, str)
                            and parsed_value.strip().startswith("'")
                            and parsed_value.strip().endswith("'")
                        ):  # Handle single string app
                            extracted_settings[key].append(
                                clean_code_string(parsed_value)
                            )
                    elif key == "DATABASES":
                        # Try to parse DATABASES as a dict
                        try:
                            db_config = json.loads(value_str.replace("'", '"'))
                            # Keep only essential parts, remove credentials if possible
                            for db_alias, config in db_config.items():
                                cleaned_config = {}
                                for db_key, db_val in config.items():
                                    if db_key not in (
                                        "USER",
                                        "PASSWORD",
                                        "HOST",
                                        "PORT",
                                        "NAME",
                                    ):  # Remove sensitive details
                                        cleaned_config[db_key] = parse_settings_value(
                                            str(db_val), settings_path
                                        )
                                    else:
                                        cleaned_config[db_key] = (
                                            "***"  # Mask sensitive info
                                        )
                                extracted_settings[key][db_alias] = cleaned_config
                        except (json.JSONDecodeError, TypeError):
                            logging.warning(
                                f"Could not parse DATABASES value robustly for key: {key}. Value: {value_str}"
                            )
                            extracted_settings[key] = (
                                "Complex or unparsable DATABASES configuration. Review manually."
                            )
                    else:
                        extracted_settings[key] = parsed_value
                except Exception as e:
                    logging.warning(
                        f"Could not parse setting '{key}'. Value: '{value_str}'. Error: {e}"
                    )
                    extracted_settings[key] = f"Parsing Error: {value_str}"

        # Clean up INSTALLED_APPS to remove duplicates and sort
        if extracted_settings["INSTALLED_APPS"]:
            extracted_settings["INSTALLED_APPS"] = sorted(
                list(set(extracted_settings["INSTALLED_APPS"]))
            )

    except Exception as e:
        logging.error(f"Error reading settings file {settings_path}: {e}")

    return extracted_settings


def extract_urls(urls_file_path, base_path=None):
    """Scans a urls.py file for path() and include() calls."""
    extracted_urls = {"paths": [], "includes": []}
    if not urls_file_path or not urls_file_path.is_file():
        logging.warning(f"URL file not found: {urls_file_path}")
        return extracted_urls

    logging.info(f"Scanning URL file: {urls_file_path}")
    try:
        with open(urls_file_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Regex for path() calls
        # Handles: path('url/', view, name='name'), path('url/', include('app.urls'))
        path_pattern = re.compile(
            r"path\(['\"](.*?)['\"](?:,\s*(?:include\(|view=|handler=)(.*?))?(?:,\s*name=['\"](.*?)['\"])?\)",
            re.MULTILINE,
        )
        # Regex for include() calls directly in urlpatterns list
        include_pattern = re.compile(r"include\(['\"](.*?)['\"]\)")

        # Find direct includes in the urlpatterns list
        includes_in_list = include_pattern.findall(content)
        for inc in includes_in_list:
            extracted_urls["includes"].append(clean_code_string(inc))

        # Find path definitions
        for match in path_pattern.finditer(content):
            url_pattern = clean_code_string(match.group(1))
            handler_raw = match.group(2)  # This can be a view function, include(), etc.
            name = clean_code_string(match.group(3))

            handler_info = "N/A"
            if handler_raw:
                handler_raw = handler_raw.strip()
                if handler_raw.startswith("include("):
                    # Extract the module path from include()
                    include_match = re.search(
                        r"include\(['\"](.*?)['\"]\)", handler_raw
                    )
                    if include_match:
                        handler_info = (
                            f"include: {clean_code_string(include_match.group(1))}"
                        )
                        extracted_urls["includes"].append(
                            clean_code_string(include_match.group(1))
                        )
                    else:
                        handler_info = "include(...)"
                else:
                    handler_info = f"view: {clean_code_string(handler_raw)}"
            extracted_urls["paths"].append(
                {"pattern": url_pattern, "handler": handler_info, "name": name or "N/A"}
            )

    except Exception as e:
        logging.error(f"Error reading URL file {urls_file_path}: {e}")

    return extracted_urls


def find_project_apps(settings_data, project_root: Path):
    """Identifies project apps from INSTALLED_APPS, prioritizing direct directories in project root."""
    installed_apps = settings_data.get("INSTALLED_APPS", [])
    project_apps = []

    # Get a set of potential app directory names directly under project_root
    direct_app_dirs = {
        d.name
        for d in project_root.iterdir()
        if d.is_dir()
        and not d.name.startswith(
            (".", "_", "venv", "tests", "docs", "static", "media", "config", "core")
        )
    }  # Exclude known non-app dirs

    for app_str in installed_apps:
        # Try to extract the app name part (e.g., 'myapp' from 'myapp.apps' or 'myapp')
        app_name_candidate = app_str.split(".")[-1]

        # Check if this candidate name corresponds to a directory directly in the project root
        if app_name_candidate in direct_app_dirs:
            project_apps.append(app_name_candidate)
            logging.debug(
                f"Identified project app (direct): {app_name_candidate} from INSTALLED_APPS: {app_str}"
            )
        # Also check if the full string might be an app name (e.g., if INSTALLED_APPS has just 'myapp')
        elif app_str in direct_app_dirs:
            project_apps.append(app_str)
            logging.debug(
                f"Identified project app (direct, full name): {app_str} from INSTALLED_APPS: {app_str}"
            )

    # Add any directories directly in project root that look like apps but weren't in INSTALLED_APPS (use with caution)
    # This is a fallback and might include non-app directories if INSTALLED_APPS is incomplete.
    fallback_apps = direct_app_dirs - set(project_apps)
    if fallback_apps:
        logging.warning(
            f"Found potential app directories not listed in INSTALLED_APPS: {fallback_apps}. Including them as potential apps."
        )
        project_apps.extend(list(fallback_apps))

    return sorted(list(set(project_apps)))


def get_file_content(file_path):
    """Safely reads file content."""
    if not file_path.is_file():
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logging.error(f"Could not read file {file_path}: {e}")
        return None


# --- Main Execution ---
if __name__ == "__main__":
    logging.info("Starting project analysis script...")

    # --- Detect Settings Directory ---
    PROJECT_SETTINGS_DIR = find_project_settings_dir(PROJECT_ROOT)
    if PROJECT_SETTINGS_DIR:
        SETTINGS_FILE = PROJECT_SETTINGS_DIR / SETTINGS_FILE_NAME
        logging.info(f"Detected settings directory: {PROJECT_SETTINGS_DIR}")
    else:
        logging.error(
            "Failed to detect settings directory. Please set SETTINGS_FILE manually."
        )
        SETTINGS_FILE = None  # Ensure it's None if not found

    # Ensure output directory exists
    OUTPUT_DIR.mkdir(exist_ok=True)

    # 1. Extract Settings Data
    logging.info("--- Extracting Settings ---")
    settings_data = extract_settings_data(SETTINGS_FILE)
    if (
        settings_data and SETTINGS_FILE
    ):  # Only write if settings were found and processed
        with open(OUTPUT_DIR / "extracted_settings.txt", "w", encoding="utf-8") as f:
            f.write("Project Settings:\n")
            for key, value in settings_data.items():
                f.write(f"# {key}\n")
                if isinstance(value, list):
                    for item in value:
                        f.write(f"- {item}\n")
                elif isinstance(value, dict):
                    f.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
                else:
                    f.write(f"{value}\n")
                f.write("\n")
        logging.info("Extracted settings saved to extracted_settings.txt")
    else:
        logging.warning("No settings data extracted or settings file not found.")

    # 2. Identify Project Apps (assuming direct structure or common subdirs handled by find_project_apps)
    project_apps = find_project_apps(settings_data, PROJECT_ROOT)
    logging.info(f"Identified potential project apps: {project_apps}")

    if not project_apps and PROJECT_ROOT.name not in [
        "venv",
        "env",
    ]:  # Avoid treating venv as project root app
        logging.warning(
            "Could not automatically identify project apps from INSTALLED_APPS or directory structure. Manual review might be needed."
        )
        # Fallback: List top-level directories, excluding known non-app directories
        potential_app_dirs = [
            d.name
            for d in PROJECT_ROOT.iterdir()
            if d.is_dir()
            and not d.name.startswith(
                (
                    ".",
                    "_",
                    "venv",
                    "tests",
                    "docs",
                    "static",
                    "media",
                    "config",
                    "core",
                    SETTINGS_FILE_NAME.replace(".py", ""),
                )
            )
        ]
        logging.info(
            f"Fallback: Potential top-level directories found: {potential_app_dirs}"
        )
        project_apps = sorted(potential_app_dirs)  # Use directory names as app names

    # 3. Extract Models
    logging.info("--- Extracting Models ---")
    apps_to_scan_for_models = []
    if project_apps:
        for app_name in project_apps:
            app_path_direct = PROJECT_ROOT / app_name
            # Removed check for 'apps/' subdirectory as per user's project structure
            if app_path_direct.is_dir():
                apps_to_scan_for_models.append(app_path_direct)
            else:
                logging.warning(
                    f"App directory '{app_name}' expected at {app_path_direct} but not found."
                )
    else:
        logging.warning(
            "No project apps identified to scan for models. Skipping model extraction."
        )

    if not apps_to_scan_for_models:
        logging.error(
            "No valid app directories found to scan for models. Please ensure script is run from project root and apps are discoverable."
        )
    else:
        extracted_models = extract_models(apps_to_scan_for_models)
        if extracted_models:
            with open(OUTPUT_DIR / "extracted_models.txt", "w", encoding="utf-8") as f:
                f.write("Project Models:\n\n")
                for app, models in extracted_models.items():
                    f.write(f"App: {app}\n")
                    if models:
                        for model in models:
                            f.write(f"- {model}\n")
                    else:
                        f.write("- No models found in models.py\n")
                    f.write("\n")
            logging.info("Extracted model information saved to extracted_models.txt")
        else:
            logging.warning("No model information extracted.")

    # 4. Extract URLs
    logging.info("--- Extracting URLs ---")
    # Try to find the main urls.py based on the detected settings directory
    main_urls_file = None
    if PROJECT_SETTINGS_DIR:
        potential_main_urls = (
            PROJECT_SETTINGS_DIR.parent / "urls.py"
        )  # e.g. project_root/urls.py if settings are in project_root/config/settings.py
        if potential_main_urls.is_file():
            main_urls_file = potential_main_urls
        else:  # Fallback: check if urls.py is directly within the settings directory (less common)
            potential_main_urls_in_settings_dir = PROJECT_SETTINGS_DIR / "urls.py"
            if potential_main_urls_in_settings_dir.is_file():
                main_urls_file = potential_main_urls_in_settings_dir

    # Final fallback: check if urls.py is directly in the project root
    if not main_urls_file and (PROJECT_ROOT / "urls.py").is_file():
        main_urls_file = PROJECT_ROOT / "urls.py"

    all_urls_data = {"root_urls": {}, "app_urls": {}}

    if main_urls_file:
        all_urls_data["root_urls"] = extract_urls(main_urls_file)
        logging.info(f"Root URLs scanned: {main_urls_file}")

        # Try to find included app URLs based on project_apps identified earlier
        for app_name in project_apps:
            # Construct path to the included urls.py file for this app
            # Assumes structure like project_root/app_name/urls.py
            app_urls_path = PROJECT_ROOT / app_name / "urls.py"
            if app_urls_path.is_file():
                all_urls_data["app_urls"][app_name] = extract_urls(app_urls_path)
                logging.info(
                    f"Found and scanned app URLs for: {app_name} at {app_urls_path}"
                )
            else:
                logging.debug(
                    f"No urls.py found directly within app directory for: {app_name} at {app_urls_path}"
                )

    else:
        logging.warning(f"Main urls.py not found. URL extraction might be incomplete.")

    if all_urls_data["root_urls"] or all_urls_data["app_urls"]:
        with open(OUTPUT_DIR / "extracted_urls.txt", "w", encoding="utf-8") as f:
            f.write("Project URLs:\n\n")
            f.write("--- Root URLs ---\n")
            f.write(f"File: {main_urls_file}\n")
            f.write("Paths:\n")
            if all_urls_data["root_urls"].get("paths"):
                for p in all_urls_data["root_urls"]["paths"]:
                    f.write(
                        f"  - Pattern: '{p['pattern']}' -> Handler: {p['handler']} (Name: {p['name']})\n"
                    )
            else:
                f.write("  No paths found.\n")
            f.write("Includes:\n")
            if all_urls_data["root_urls"].get("includes"):
                for inc in all_urls_data["root_urls"]["includes"]:
                    f.write(f"  - {inc}\n")
            else:
                f.write("  No includes found.\n")
            f.write("\n")

            f.write("--- App-specific URLs ---\n")
            if all_urls_data["app_urls"]:
                for app, data in all_urls_data["app_urls"].items():
                    f.write(f"App: {app}\n")
                    f.write("  Paths:\n")
                    if data.get("paths"):
                        for p in data["paths"]:
                            f.write(
                                f"    - Pattern: '{p['pattern']}' -> Handler: {p['handler']} (Name: {p['name']})\n"
                            )
                    else:
                        f.write("    No paths found.\n")
                    f.write("  Includes:\n")
                    if data.get("includes"):
                        for inc in data["includes"]:
                            f.write(f"    - {inc}\n")
                    else:
                        f.write("    No includes found.\n")
                    f.write("\n")
            else:
                f.write("No app-specific URLs found or processed.\n")
        logging.info("Extracted URL information saved to extracted_urls.txt")
    else:
        logging.warning("No URL information extracted.")

    # 5. Extract basic structure of key files (e.g., views.py, services.py) - VERY Basic
    # This part is highly heuristic and might need significant refinement based on actual project structure
    logging.info("--- Extracting Basic File Structures (Heuristic) ---")
    file_types_to_scan = {
        "views": ["views.py", "api/views.py"],
        "services": ["services.py", "utils/services.py"],
        "forms": ["forms.py"],
        "admin": ["admin.py"],
        "models_init": [
            "models/__init__.py"
        ],  # For checking if models are imported here
    }
    extracted_file_structures = {}

    if project_apps:  # Only scan if apps were identified
        for app_name in project_apps:
            app_base_path = PROJECT_ROOT / app_name  # Direct path to the app directory

            for category, filenames in file_types_to_scan.items():
                for filename_pattern in filenames:
                    try:
                        potential_file = app_base_path / Path(filename_pattern)
                        if potential_file.is_file():
                            content = get_file_content(potential_file)
                            if content:
                                # Simple: just note the file exists and potentially what's inside (classes/functions)
                                # This is VERY basic and doesn't parse deeply.
                                classes = re.findall(r"class\s+(\w+):", content)
                                functions = re.findall(r"def\s+(\w+)\(", content)
                                structures = {
                                    "classes": classes,
                                    "functions": functions,
                                }

                                if app_name not in extracted_file_structures:
                                    extracted_file_structures[app_name] = {}
                                if category not in extracted_file_structures[app_name]:
                                    extracted_file_structures[app_name][category] = []

                                extracted_file_structures[app_name][category].append(
                                    {
                                        "path": str(
                                            potential_file.relative_to(PROJECT_ROOT)
                                        ),
                                        "structures": structures,
                                    }
                                )
                                logging.debug(f"Found structure file: {potential_file}")
                    except Exception as e:
                        logging.error(
                            f"Error processing file structure for {filename_pattern} in app {app_name}: {e}"
                        )

    if extracted_file_structures:
        with open(
            OUTPUT_DIR / "extracted_file_structures.txt", "w", encoding="utf-8"
        ) as f:
            f.write("Project File Structures (Basic):\n\n")
            for app, categories in extracted_file_structures.items():
                f.write(f"App: {app}\n")
                for category, files in categories.items():
                    f.write(f"  {category.capitalize()}:\n")
                    for file_info in files:
                        f.write(f"    - Path: {file_info['path']}\n")
                        if file_info["structures"]["classes"]:
                            f.write(
                                f"      Classes: {', '.join(file_info['structures']['classes'])}\n"
                            )
                        if file_info["structures"]["functions"]:
                            f.write(
                                f"      Functions: {', '.join(file_info['structures']['functions'])}\n"
                            )
                f.write("\n")
        logging.info(
            "Extracted file structure information saved to extracted_file_structures.txt"
        )
    else:
        logging.warning(
            "No file structure information extracted. This section is heuristic."
        )

    logging.info("Project analysis script finished.")
    print(
        f"\nScript finished. Extracted data saved in the '{OUTPUT_DIR.name}' directory."
    )
    print("Please review the generated files for accuracy.")
