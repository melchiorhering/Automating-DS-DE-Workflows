#!/usr/bin/env python
import argparse
import os
from pathlib import Path

from huggingface_hub import snapshot_download


def download_subdir_from_hub(repo_id: str, subdir: str, local_dir: Path, hf_token: str):
    print(f"📥 Downloading '{subdir}' from '{repo_id}' into '{local_dir}'...")

    snapshot_download(
        repo_id=repo_id,
        repo_type="dataset",
        local_dir=local_dir,
        token=hf_token,
        allow_patterns=[f"{subdir}/**"],
    )

    print("✅ Download complete!")


def main():
    parser = argparse.ArgumentParser(description="Download a subdirectory from a Hugging Face dataset repo.")
    parser.add_argument("--repo", required=True, help="Name of the dataset repo (e.g. 'DS-DE-Automation').")
    parser.add_argument("--subdir", required=True, help="Subdirectory inside the repo to download.")
    parser.add_argument("--target", required=True, help="Local directory to store the downloaded files.")

    args = parser.parse_args()

    hf_username = os.getenv("HF_USERNAME")
    hf_token = os.getenv("HF_TOKEN")

    if not hf_username or not hf_token:
        raise EnvironmentError("Both HF_USERNAME and HF_TOKEN must be set as environment variables.")

    full_repo_id = f"{hf_username}/{args.repo}"
    target_path = Path(args.target)

    download_subdir_from_hub(full_repo_id, args.subdir, target_path, hf_token)


if __name__ == "__main__":
    main()
