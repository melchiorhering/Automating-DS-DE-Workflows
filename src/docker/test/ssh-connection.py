#!/usr/bin/env python3
import argparse
import sys

import paramiko


def run_ssh_command(ssh, command, timeout=10):
    stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
    exit_status = stdout.channel.recv_exit_status()
    output = stdout.read().decode().strip()
    error = stderr.read().decode().strip()
    return exit_status, output, error


def connect_ssh_with_password(host, port, username, password, timeout=10):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname=host, port=port, username=username, password=password, timeout=timeout)
    return ssh


def test_root_privileges(ssh):
    exit_status, output, error = run_ssh_command(ssh, "sudo -n whoami")
    if exit_status == 0 and output == "root":
        return True
    return False, exit_status, output, error


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SSH connectivity & sudo test")
    parser.add_argument("--host", default="localhost", help="SSH host")
    parser.add_argument("--port", type=int, default=2222, help="SSH port")
    parser.add_argument("--user", default="user", help="SSH username")
    parser.add_argument("--password", default="password", help="SSH password")
    args = parser.parse_args()

    try:
        ssh = connect_ssh_with_password(args.host, args.port, args.user, args.password)
    except Exception as exc:
        print(f"Failed to connect: {exc}", file=sys.stderr)
        sys.exit(1)

    # Basic connectivity test
    status, output, error = run_ssh_command(ssh, "echo OK")
    print(f"[Basic] Exit status: {status}, Output: {output}, Error: {error}")

    # Root privilege test
    root_test = test_root_privileges(ssh)
    if root_test is True:
        print("[Root] User has passwordless sudo (root privileges confirmed).")
    else:
        exit_status, output, error = root_test[1:]
        print(f"[Root] NOT confirmed (exit {exit_status}, out='{output}', err='{error}').")

    ssh.close()
