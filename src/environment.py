import os
import random
import string
import subprocess
import tempfile
import time
from typing import List, Optional

# CAN I USE A DOCKERIMAGE LOCATED IN ../environment/


class WebTopManager:
    """
    A class to manage multiple Ubuntu GNOME Webtop containers using Docker or Podman.
    """

    def __init__(self, use_podman: bool = False, base_port: int = 3000):
        """
        Initialize the WebTopManager.

        Args:
            use_podman: If True, use Podman instead of Docker. Default is False.
            base_port: The starting port number for the containers. Default is 3000.
        """
        self.engine = "podman" if use_podman else "docker"
        self.base_port = base_port
        self.containers = []

    def _random_string(self, length: int = 8) -> str:
        """Generate a random string for container names."""
        return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))

    def _run_command(self, command: List[str], live_output: bool = False) -> str:
        """Run a shell command and return its output."""
        print(f"Running command: {' '.join(command)}")

        if live_output:
            # Run with live output
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

            output = []
            for line in iter(process.stdout.readline, ""):
                line = line.rstrip()
                print(line)
                output.append(line)

            process.stdout.close()
            return_code = process.wait()

            if return_code != 0:
                print(f"Command failed with return code {return_code}")
                raise subprocess.CalledProcessError(return_code, command, output="\n".join(output))

            return "\n".join(output)
        else:
            # Run without live output
            try:
                result = subprocess.run(command, capture_output=True, text=True, check=True)
                return result.stdout.strip()
            except subprocess.CalledProcessError as e:
                print(f"Command failed with return code {e.returncode}")
                print(f"Error output: {e.stderr}")
                raise

    def create_gnome_image(self, image_name: str = "custom/webtop-ubuntu-gnome:latest") -> str:
        """
        Create a custom Ubuntu GNOME Webtop image.

        Args:
            image_name: The name for the custom image. Default is "custom/webtop-ubuntu-gnome:latest".

        Returns:
            The name of the created image.
        """
        if self.engine == "podman":
            return self.create_gnome_image_podman(image_name)
        else:
            return self.create_gnome_image_docker(image_name)

    def create_gnome_image_docker(self, image_name: str = "custom/webtop-ubuntu-gnome:latest") -> str:
        """
        Create a custom Ubuntu GNOME Webtop image using Docker.

        Args:
            image_name: The name for the custom image.
        """
        print("Creating temporary container to build the GNOME image...")
        temp_container = f"gnome-builder-{self._random_string()}"

        # Run the base container
        try:
            self._run_command(
                [
                    self.engine,
                    "run",
                    "-d",
                    "--name",
                    temp_container,
                    "--security-opt",
                    "seccomp=unconfined",
                    "lscr.io/linuxserver/webtop:ubuntu-xfce",
                ]
            )

            # Wait for container to be ready
            print("Waiting for container to initialize...")
            time.sleep(5)

            print("Testing container readiness...")
            self._run_command([self.engine, "exec", temp_container, "echo", "Container is ready"])

            print("Installing GNOME packages (this will take a few minutes)...")
            # Split the installation process into smaller steps with live output

            # Update package lists
            self._run_command([self.engine, "exec", temp_container, "bash", "-c", "apt-get update"], live_output=True)

            # Install GNOME packages
            self._run_command(
                [
                    self.engine,
                    "exec",
                    temp_container,
                    "bash",
                    "-c",
                    "DEBIAN_FRONTEND=noninteractive apt-get install -y gnome-session gnome-shell gnome-terminal nautilus gnome-control-center ubuntu-gnome-default-settings adwaita-icon-theme-full",
                ],
                live_output=True,
            )

            # Clean up
            self._run_command([self.engine, "exec", temp_container, "bash", "-c", "apt-get clean"], live_output=True)

            # Create startup script
            print("Configuring startup script...")
            self._run_command(
                [
                    self.engine,
                    "exec",
                    temp_container,
                    "bash",
                    "-c",
                    """cat > /defaults/startwm.sh << 'EOF'
#!/bin/bash
export XDG_SESSION_TYPE=x11
export XDG_SESSION_DESKTOP=gnome
export XDG_CURRENT_DESKTOP=GNOME
exec gnome-session
EOF""",
                ]
            )

            # Make startup script executable
            self._run_command([self.engine, "exec", temp_container, "chmod", "+x", "/defaults/startwm.sh"])

            print(f"Creating image: {image_name}")
            # Stop container and commit to new image
            self._run_command([self.engine, "stop", temp_container])
            self._run_command([self.engine, "commit", temp_container, image_name])

            # Remove temporary container
            self._run_command([self.engine, "rm", temp_container])

            print(f"Image {image_name} created successfully")
            return image_name

        except Exception as e:
            print(f"Error creating GNOME image: {str(e)}")
            try:
                # Try to clean up the container if it exists
                self._run_command([self.engine, "stop", temp_container], live_output=False)
                self._run_command([self.engine, "rm", temp_container], live_output=False)
            except:
                pass
            raise

    def create_gnome_image_podman(self, image_name: str = "custom/webtop-ubuntu-gnome:latest") -> str:
        """Create a custom Ubuntu GNOME Webtop image using Podman's Containerfile approach."""

        # Create a temporary directory for the Containerfile
        temp_dir = tempfile.mkdtemp()

        # Create Containerfile
        containerfile_path = os.path.join(temp_dir, "Containerfile")
        with open(containerfile_path, "w") as f:
            f.write("""FROM lscr.io/linuxserver/webtop:ubuntu-xfce
RUN apt-get update && \\
    DEBIAN_FRONTEND=noninteractive apt-get install -y \\
    gnome-session gnome-shell gnome-terminal nautilus \\
    gnome-control-center ubuntu-gnome-default-settings \\
    adwaita-icon-theme-full && \\
    apt-get clean && \\
    echo '#!/bin/bash' > /defaults/startwm.sh && \\
    echo 'export XDG_SESSION_TYPE=x11' >> /defaults/startwm.sh && \\
    echo 'export XDG_SESSION_DESKTOP=gnome' >> /defaults/startwm.sh && \\
    echo 'export XDG_CURRENT_DESKTOP=GNOME' >> /defaults/startwm.sh && \\
    echo 'exec gnome-session' >> /defaults/startwm.sh && \\
    chmod +x /defaults/startwm.sh
""")

        # Build the image
        try:
            print(f"Building image using Containerfile at {containerfile_path}")
            self._run_command(
                ["podman", "build", "-t", image_name, "-f", containerfile_path, temp_dir], live_output=True
            )

            print(f"Image {image_name} created successfully")
            return image_name

        finally:
            # Clean up temp directory
            import shutil

            shutil.rmtree(temp_dir)

    def spawn_containers(
        self, count: int, image_name: Optional[str] = None, volume_base_path: str = "./webtop-data"
    ) -> List[dict]:
        """
        Spawn multiple Ubuntu GNOME Webtop containers.

        Args:
            count: Number of containers to spawn.
            image_name: The image to use. If None, create a new image.
            volume_base_path: Base path for volume mounts.

        Returns:
            List of dictionaries with container information.
        """
        if image_name is None:
            image_name = self.create_gnome_image()

        print(f"Spawning {count} containers using {image_name}...")

        for i in range(count):
            container_name = f"webtop-gnome-{self._random_string()}"
            http_port = self.base_port + (i * 2)
            https_port = self.base_port + (i * 2) + 1
            volume_path = os.path.join(volume_base_path, container_name)

            # Ensure volume directory exists
            os.makedirs(volume_path, exist_ok=True)

            print(f"Starting container {i + 1}/{count}: {container_name} (ports: {http_port}, {https_port})")

            self._run_command(
                [
                    self.engine,
                    "run",
                    "-d",
                    "--name",
                    container_name,
                    "--security-opt",
                    "seccomp=unconfined",
                    "-e",
                    "PUID=1000",
                    "-e",
                    "PGID=1000",
                    "-e",
                    "TZ=Etc/UTC",
                    "-p",
                    f"{http_port}:3000",
                    "-p",
                    f"{https_port}:3001",
                    "-v",
                    f"{volume_path}:/config",
                    "--shm-size",
                    "1gb",
                    image_name,
                ]
            )

            self.containers.append(
                {"name": container_name, "http_port": http_port, "https_port": https_port, "volume_path": volume_path}
            )

        print(f"Successfully spawned {count} containers")
        for i, container in enumerate(self.containers):
            print(
                f"{i + 1}: {container['name']} - http://localhost:{container['http_port']} or https://localhost:{container['https_port']}"
            )

        return self.containers

    def stop_containers(self) -> None:
        """Stop all running containers created by this instance."""
        for container in self.containers:
            print(f"Stopping container: {container['name']}")
            try:
                self._run_command([self.engine, "stop", container["name"]])
                self._run_command([self.engine, "rm", container["name"]])
            except subprocess.CalledProcessError:
                print(f"Error stopping/removing container: {container['name']}")

    def show_info(self) -> None:
        """Display information about all running containers."""
        if not self.containers:
            print("No containers are currently running")
            return

        print(f"Running containers ({len(self.containers)}):")
        for i, container in enumerate(self.containers):
            print(
                f"{i + 1}: {container['name']} - http://localhost:{container['http_port']} or https://localhost:{container['https_port']}"
            )

    def save_image_to_file(self, image_name: str, output_path: str) -> str:
        """Save the image to a tar file for future use."""
        print(f"Saving image {image_name} to {output_path}...")
        self._run_command([self.engine, "save", "-o", output_path, image_name], live_output=True)
        print(f"Image saved successfully to {output_path}")
        return output_path

    def load_image_from_file(self, input_path: str, image_name: str) -> str:
        """Load an image from a tar file."""
        print(f"Loading image from {input_path} as {image_name}...")
        self._run_command([self.engine, "load", "-i", input_path], live_output=True)

        # Tag the loaded image with the desired name if needed
        if self.engine == "podman":
            # Get the actual loaded image ID/name
            loaded_image_id = self._run_command(
                [self.engine, "images", "--format", "{{.Id}}", "--sort", "created", "--limit", "1"]
            )
            if loaded_image_id:
                self._run_command([self.engine, "tag", loaded_image_id, image_name])
        else:
            # For Docker, tag it again to ensure the right name
            try:
                self._run_command([self.engine, "tag", "localhost/webtop-ubuntu-gnome:latest", image_name])
            except:
                # If tagging fails, the image might already have the right name
                pass

        print(f"Image loaded successfully as {image_name}")
        return image_name

    def push_image_to_registry(self, image_name: str, registry_url: str) -> str:
        """Push the image to a container registry."""
        registry_image = f"{registry_url}/{image_name}"

        # Tag the image for the registry
        self._run_command([self.engine, "tag", image_name, registry_image])

        # Push to registry
        print(f"Pushing image to {registry_image}...")
        self._run_command([self.engine, "push", registry_image], live_output=True)

        print(f"Image pushed successfully to {registry_image}")
        return registry_image

    def apply_configuration(self, container_name: str, config_script_path: str) -> None:
        """
        Apply configuration script to a running container.
        The script should be a bash script that configures the desktop environment.
        """
        # Copy the script to the container
        temp_script = f"/tmp/config-script-{self._random_string()}.sh"
        self._run_command([self.engine, "cp", config_script_path, f"{container_name}:{temp_script}"])

        # Make it executable and run it
        print(f"Applying configuration script to {container_name}...")
        self._run_command([self.engine, "exec", container_name, "chmod", "+x", temp_script])

        self._run_command([self.engine, "exec", container_name, temp_script], live_output=True)

        # Remove the script
        self._run_command([self.engine, "exec", container_name, "rm", temp_script])

        print("Configuration applied successfully")

    def create_preconfigured_image(
        self,
        base_image_name: str = None,
        config_script_path: str = None,
        output_image_name: str = "custom/webtop-ubuntu-gnome-configured:latest",
    ) -> str:
        """
        Create a preconfigured GNOME Webtop image with custom settings.

        Args:
            base_image_name: The base image to use. If None, create a new GNOME image.
            config_script_path: Path to a bash script with configuration commands.
            output_image_name: Name for the configured image.

        Returns:
            The name of the created image.
        """
        # Get or create base image
        if base_image_name is None:
            base_image_name = self.create_gnome_image()

        # Create template container
        container_name = f"config-template-{self._random_string()}"

        try:
            # Run container
            self._run_command(
                [
                    self.engine,
                    "run",
                    "-d",
                    "--name",
                    container_name,
                    "--security-opt",
                    "seccomp=unconfined",
                    base_image_name,
                ]
            )

            # Wait for container to be ready
            print("Waiting for container to initialize...")
            time.sleep(5)

            # Apply configuration if script provided
            if config_script_path:
                self.apply_configuration(container_name, config_script_path)
            else:
                print("No configuration script provided. Creating default configuration...")

                # Create a basic configuration script
                temp_script = "/tmp/default_config.sh"
                with open(temp_script, "w") as f:
                    f.write("""#!/bin/bash
# Default GNOME configuration
echo "Applying default GNOME configuration..."
mkdir -p /config/.config
mkdir -p /config/.local/share/applications

# Try to configure GNOME settings (may not work in all containers)
if command -v dconf > /dev/null; then
    # Set dark theme
    dconf write /org/gnome/desktop/interface/gtk-theme "'Adwaita-dark'" || true
    # Disable screen lock
    dconf write /org/gnome/desktop/screensaver/lock-enabled "false" || true
    # Set favorite applications
    dconf write /org/gnome/shell/favorite-apps "['firefox.desktop', 'org.gnome.Terminal.desktop', 'org.gnome.Nautilus.desktop']" || true
fi

# Create a desktop shortcut
cat > /config/Desktop/firefox.desktop << EOF
[Desktop Entry]
Type=Application
Name=Firefox
Comment=Browse the Web
Exec=firefox
Icon=firefox
Terminal=false
Categories=Network;WebBrowser;
EOF

chmod +x /config/Desktop/firefox.desktop

echo "Default GNOME configuration applied"
""")

                os.chmod(temp_script, 0o755)
                self.apply_configuration(container_name, temp_script)
                os.remove(temp_script)

            # Commit the container to a new image
            print(f"Creating configured image: {output_image_name}")
            self._run_command([self.engine, "stop", container_name])
            self._run_command([self.engine, "commit", container_name, output_image_name])

            print(f"Preconfigured image {output_image_name} created successfully")
            return output_image_name

        finally:
            # Clean up
            try:
                self._run_command([self.engine, "rm", "-f", container_name])
            except:
                pass
