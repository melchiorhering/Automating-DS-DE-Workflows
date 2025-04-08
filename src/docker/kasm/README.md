## Using KasmWeb Container Environment

### Why KasmWeb Instead of VMware?

- **Browser-Based Access**: Access development environment directly through your browser
- **No VMware Dependencies**: Eliminates need for proprietary virtualization software
- **Parallel Development**: Run multiple isolated environments simultaneously
- **Resource Efficient**: Containers are lighter than full VMs
- **Cross-Platform**: Works on any OS with Docker/Podman installed

For further reading you can find the KasmTech workspace-images GitHub repository [here](https://github.com/kasmtech/workspaces-images)
Or see their extensive [docs](https://kasmweb.com/)!

### Quick Start

1. Startup the OS-Enviroment
   ```bash
   docker-compose up --build
   ```
2. Access workspace:
   - Login: `kasm_user`
   - Password: `password` # or whatever is set in `docker-compose.yaml`
   - URL:`https://kasm_user:password@localhost:6901/`
   - GET-IMAGE: `https://kasm_user:password@localhost:6901/api/get_screenshot`

### Environment Details

#### Docker Configuration

```yaml
# filepath: docker-compose.yaml
services:
  # https://www.kasmweb.com/
  kasm-ubuntu:
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "6901:6901" # Expose port 6901
    environment:
      - VNC_PW=password # Set the VNC password
    shm_size: "512m" # Set shared memory size
    stdin_open: true # Keep stdin open for interaction
    tty: true # Allocate a pseudo-TTY
    restart: unless-stopped # Optional: Restart policy
```

#### View logs

`docker-compose logs -f`

#### Rebuild environment

`docker-compose up -d --build`

#### Reset completely

`docker-compose down -v`
`docker-compose up -d`

## Resource Management

| Resource  | Default      | Recommended | Maximum        |
| --------- | ------------ | ----------- | -------------- |
| CPU Cores | 2            | 4           | Host limit     |
| Memory    | 4GB          | 8GB         | Host limit     |
| Storage   | 10GB         | 20GB        | Host available |
| Network   | Bridge       | Bridge      | Host network   |
| GPU       | Not required | Optional    | Passthrough    |

## VMware vs KasmWeb Comparison

| Feature             | KasmWeb Container | VMware VM            |
| ------------------- | ----------------- | -------------------- |
| Startup Time        | ~30 seconds       | 2-3 minutes          |
| Memory Usage        | 2-4GB             | 8GB+                 |
| Disk Space          | ~10GB             | 40GB+                |
| Access Method       | Web browser       | VMware client        |
| License Cost        | Free (Apache 2.0) | Commercial           |
| Parallel Instances  | Limited by RAM    | Limited by RAM & CPU |
| Setup Time          | 5 minutes         | 30+ minutes          |
| Cross-Platform      | Yes (Docker)      | Yes (VMware)         |
| Resource Overhead   | Low               | High                 |
| Integration         | Container native  | VM based             |
| Persistence         | Volume mounts     | Full disk image      |
| Network Performance | Native            | Virtualized          |
