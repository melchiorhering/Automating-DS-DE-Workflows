import shutil
import sys
from pathlib import Path

from docker import from_env
from docker.types import Mount


def copy_disk_images(base_boot: Path, base_data: Path, target_dir: Path):
    print(f"[*] Preparing VM in: {target_dir}")
    target_dir.mkdir(parents=True, exist_ok=True)

    boot_target = target_dir / "boot.iso"
    data_target = target_dir / "data.img"

    shutil.copy(base_boot, boot_target)
    shutil.copy(base_data, data_target)

    return boot_target, data_target


def validate_image(path: Path):
    if not path.exists():
        sys.exit(f"❌ Image not found: {path}")


def launch_container(name: str, boot_img: Path, data_img: Path, vnc: int, ssh: int, work_dir: Path):
    print(f"[*] Launching container: {name}")
    client = from_env()

    mounts = [
        Mount(target="/boot.iso", source=str(boot_img.resolve()), type="bind", read_only=False),
        Mount(target="/data.img", source=str(data_img.resolve()), type="bind", read_only=False),
        Mount(target="/storage", source=str(work_dir.resolve()), type="bind", read_only=False),
    ]

    container = client.containers.run(
        "qemux/qemu",
        name=name,
        devices=["/dev/kvm", "/dev/net/tun"],
        cap_add=["NET_ADMIN"],
        mounts=mounts,
        environment={
            "BOOT": "ubuntu",
            "RAM_SIZE": "4G",
            "CPU_CORES": "4",
            "DISK_SIZE": "16G",
            "DEBUG": "Y",
        },
        ports={
            "8006/tcp": vnc,
            "22/tcp": ssh,
        },
        detach=True,
    )

    print(f"[+] {name} started (id={container.short_id})")
    print(f"    ▸ VNC: localhost:{vnc}")
    print(f"    ▸ SSH: ssh user@localhost -p {ssh}")


def main():
    script_dir = Path(__file__).resolve().parent.parent
    base_dir = script_dir / "vms/ubuntu-base"
    base_boot = base_dir / "boot.iso"
    base_data = base_dir / "data.img"

    validate_image(base_boot)
    validate_image(base_data)

    snaps = ["snap1", "snap2"]
    for idx, snap_name in enumerate(snaps, start=1):
        snap_dir = script_dir / f"vms/snapshots/{snap_name}"
        boot_img, data_img = copy_disk_images(base_boot, base_data, snap_dir)

        vnc_port = 8000 + idx * 100 + 6
        ssh_port = 2222 + idx
        launch_container(f"ubuntu-{snap_name}", boot_img, data_img, vnc_port, ssh_port, snap_dir)


if __name__ == "__main__":
    main()
