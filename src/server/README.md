# Sandbox Environment Server

## Overview

This server provides a sandbox environment for executing Python code in a secure, controlled setting. It supports both console and GUI code execution over a WebSocket interface, enabling remote code execution and screenshot capture with mouse pointer overlays.

## Features

- **WebSocket API**: Handles code execution requests and returns outputs.
- **GUI Code Execution**: Executes GUI-based code and captures screenshots with mouse annotations.
- **Screenshot Capture**: Takes desktop screenshots, annotates with mouse position, and overlays the captured cursor image.
- **XCursor Integration**: Retrieves the current cursor image using X11 libraries.
- **Dynamic Package Management**: Installs required Python packages on demand using `uv`.

## Installation

1. **Dependencies**:
   Install the required packages with:

   ```
   pip install -r requirements.txt
   ```

   Alternatively, dependencies are defined in `pyproject.toml`.

2. **X Server Requirement**:
   Ensure an X server is running. The server relies on XFixes and X11 libraries to capture screenshots and cursor images.

## Usage

- **Starting the Server**:
  Run the server with:
  ```
  uv run main.py --screenshots-path=<path_to_screenshots>
  ```
  By default, it listens on `localhost:8765`. Adjust the host and port as needed via command-line arguments or environment variables.

## Project Structure

- **main.py**: Launches the WebSocket server and handles client requests.
- **pyxcursor.py**: Provides the functionality for capturing the X cursor image.
- **start.sh**: A shell script for environment setup and server startup.
- **pyproject.toml**: Contains project metadata and dependency specifications.

## Contributing

Contributions and enhancements are welcome. Please submit pull requests with clear descriptions of changes.

## License

This project is released under the MIT License.
