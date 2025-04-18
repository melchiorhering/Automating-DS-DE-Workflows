from sandbox.qemu import QemuVMManager, SSHConfig, VMConfig

# import nest_asyncio, asyncio

# nest_asyncio.apply()  # still useful for other asyncio code

cfg = VMConfig(
    container_name="TestVM",
    vm_base_dir="./docker/vms/ubuntu-noble",
    host_shared_dir="./docker/shared",
    server_host_dir="./server",
    extra_ports={8000: 8000},
    runtime_env={"HOST": "0.0.0.0", "PORT": "8000"},
)

ssh_cfg = SSHConfig(username="user", password="password", port=2222)
vm = QemuVMManager(cfg, ssh_cfg=ssh_cfg)

vm.create_and_setup_vm()
# coloured REPL with TAB completion – now works in the notebook cell
vm.shell()
