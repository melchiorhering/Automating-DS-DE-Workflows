#!/usr/bin/env python
import argparse
import os
from pathlib import Path

from huggingface_hub import HfApi


def upload_folder_to_hub(folder_path: Path, repo_id: str, hf_token: str):
    api = HfApi(token=hf_token)

    if not folder_path.exists() or not folder_path.is_dir():
        raise ValueError(f"Provided folder path does not exist or is not a directory: {folder_path}")

    print(f"📤 Uploading {folder_path} to {repo_id} on Hugging Face Hub...")

    api.upload_large_folder(
        repo_id=repo_id,
        folder_path=folder_path,
        repo_type="dataset",
        allow_patterns=["*.iso", "*.qcow2", "*.vmdk", "*.vdi", "*.img"],
    )

    print("✅ Upload complete!")


def main():
    parser = argparse.ArgumentParser(description="Upload a folder to the Hugging Face Hub.")
    parser.add_argument("--path", required=True, help="Path to the local folder to upload.")
    parser.add_argument("--repo", required=True, help="Name of the target dataset repo (e.g. 'my-dataset').")
    parser.add_argument(
        "--private", action="store_true", help="Create the repo as private if it doesn't exist.", default=False
    )

    args = parser.parse_args()

    hf_username = os.getenv("HF_USERNAME")
    hf_token = os.getenv("HF_TOKEN")

    if not hf_username or not hf_token:
        raise EnvironmentError("Both HF_USERNAME and HF_TOKEN must be set as environment variables.")

    full_repo_id = f"{hf_username}/{args.repo}"
    folder_path = Path(args.path)

    api = HfApi(token=hf_token)
    try:
        api.repo_info(repo_id=full_repo_id, repo_type="dataset")
    except Exception:
        print(f"📁 Creating dataset repo '{full_repo_id}'...")
        api.create_repo(repo_id=full_repo_id, repo_type="dataset", private=args.private)

    upload_folder_to_hub(folder_path, full_repo_id, hf_token)


if __name__ == "__main__":
    main()
