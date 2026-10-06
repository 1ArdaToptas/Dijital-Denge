# Repository Guidelines

## Project Structure & Module Organization

This is a Windows desktop screen-time application. `main.py` contains the application and UI; `assets/` holds icons and image resources, and `docs/` contains screenshots used for documentation. `DijitalDenge.spec` and `Dijital Denge.spec` are PyInstaller build configurations. There is currently no separate `src/` or test directory, so keep changes focused in the existing layout unless a feature warrants a module.

## Build, Test, and Development Commands

Use Python 3.11 on Windows; the app depends on Windows APIs and is not portable as-is.

- `python main.py` launches the application.
- `python -m pip install customtkinter pillow` installs the third-party packages imported by the app in a clean environment.
- `pyinstaller DijitalDenge.spec` builds the packaged executable when PyInstaller is installed.

Run commands from the repository root. No automated test or lint command is configured at present.

## Coding Style & Naming Conventions

Follow standard Python style: four-space indentation, `snake_case` for functions and variables, and `PascalCase` for classes. Keep imports grouped at the top, prefer small functions with clear responsibilities, and preserve the app’s existing UI and language conventions. Reuse resources from `assets/` rather than duplicating image files.

## Testing Guidelines

There is no checked-in test suite or stated coverage target. For changes, run `python -m py_compile main.py` to catch syntax errors. For UI or tracking changes, launch with `python main.py` on Windows and manually check the affected screen or behavior. Add focused tests if introducing logic that can be exercised independently of the Windows UI.

## Commit & Pull Request Guidelines

Recent Git history was not available in this environment, so no repository-specific commit format could be confirmed. Use short, imperative commit subjects (for example, `Fix daily usage summary`). Pull requests should describe the user-visible change, mention relevant issue context, list manual checks performed, and include updated screenshots for visible UI changes.

## Security & Configuration Tips

Usage data and runtime state may be written locally; do not commit personal activity data, logs, or temporary files. Keep local virtual environments and generated build output out of version control, consistent with `.gitignore`.
