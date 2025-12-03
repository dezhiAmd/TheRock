"""
This script runs the Linux and Windows build configurations

Required environment variables:
  - amdgpu_families
  - package_version
  - extra_cmake_options
  - BUILD_DIR

Optional environment variables:
  - VCToolsInstallDir
  - GITHUB_WORKSPACE
  - THEROCK_BACKGROUND_BUILD_JOBS_CALCULATED (set automatically by this script for debugging)
"""

import argparse
import logging
import os
from pathlib import Path
import platform
import shlex
import subprocess
import multiprocessing
import psutil

logging.basicConfig(level=logging.INFO)
THIS_SCRIPT_DIR = Path(__file__).resolve().parent
THEROCK_DIR = THIS_SCRIPT_DIR.parent.parent

PLATFORM = platform.system().lower()

cmake_preset = os.getenv("cmake_preset")
amdgpu_families = os.getenv("amdgpu_families")
package_version = os.getenv("package_version")
extra_cmake_options = os.getenv("extra_cmake_options")
build_dir = os.getenv("BUILD_DIR")
vctools_install_dir = os.getenv("VCToolsInstallDir")
github_workspace = os.getenv("GITHUB_WORKSPACE")


def calculate_background_build_jobs():
    """
    Calculate optimal number of background build jobs based on CPU cores and available memory.
    
    Strategy:
    1. Base calculation on CPU cores (cores - 1, min 2)
    2. Constrain by available memory (assume each job needs ~2GB)
    3. Cache result in environment variable for debugging
    
    Returns:
        int: Number of background build jobs
    """
    # Check if already calculated and cached
    cached_jobs = os.getenv("THEROCK_BACKGROUND_BUILD_JOBS_CALCULATED")
    if cached_jobs:
        jobs = int(cached_jobs)
        logging.info(f"Using cached THEROCK_BACKGROUND_BUILD_JOBS: {jobs}")
        return jobs
    
    # Get CPU count
    cpu_count = multiprocessing.cpu_count()
    
    # Get available memory in GB
    memory_info = psutil.virtual_memory()
    total_memory_gb = memory_info.total / (1024 ** 3)
    available_memory_gb = memory_info.available / (1024 ** 3)
    
    # Calculate based on CPU cores (cores - 1, min 2)
    jobs_by_cpu = max(2, cpu_count-1)
    
    # Calculate based on memory (assume ~2GB per background job for safety)
    # Use available memory to be conservative
    memory_per_job_gb = 2.0
    jobs_by_memory = max(2, int(available_memory_gb / memory_per_job_gb))
    
    # Take the minimum of the two constraints
    background_jobs = min(jobs_by_cpu, jobs_by_memory)
    
    # Additional safety: cap at 16 to avoid overwhelming the system
    background_jobs = min(background_jobs, 16)
    
    # Ensure minimum of 2
    background_jobs = max(2, background_jobs)
    
    # Cache the result in environment variable for debugging
    os.environ["THEROCK_BACKGROUND_BUILD_JOBS_CALCULATED"] = str(background_jobs)
    
    # Log detailed information for debugging
    logging.info("=" * 60)
    logging.info("Background Build Jobs Calculation:")
    logging.info(f"  CPU cores: {cpu_count}")
    logging.info(f"  Total memory: {total_memory_gb:.2f} GB")
    logging.info(f"  Available memory: {available_memory_gb:.2f} GB")
    logging.info(f"  Jobs by CPU (cores-1): {jobs_by_cpu}")
    logging.info(f"  Jobs by memory (available/{memory_per_job_gb}GB): {jobs_by_memory}")
    logging.info(f"  Final calculated jobs: {background_jobs}")
    logging.info("=" * 60)
    
    return background_jobs


# Calculate Windows background build jobs dynamically
windows_background_jobs = calculate_background_build_jobs() if PLATFORM == "windows" else 4

platform_options = {
    "windows": [
        f"-DCMAKE_C_COMPILER={vctools_install_dir}/bin/Hostx64/x64/cl.exe",
        f"-DCMAKE_CXX_COMPILER={vctools_install_dir}/bin/Hostx64/x64/cl.exe",
        f"-DCMAKE_LINKER={vctools_install_dir}/bin/Hostx64/x64/link.exe",
        f"-DTHEROCK_BACKGROUND_BUILD_JOBS={windows_background_jobs}",
    ],
}


def build_configure(manylinux=False):
    logging.info(f"Building package {package_version}")

    cmd = [
        "cmake",
        "-B",
        build_dir,
        "-GNinja",
        ".",
    ]
    if cmake_preset:
        cmd.extend(["--preset", cmake_preset])
    cmd.extend(
        [
            f"-DTHEROCK_AMDGPU_FAMILIES={amdgpu_families}",
            f"-DTHEROCK_PACKAGE_VERSION='{package_version}'",
            "-DCMAKE_C_COMPILER_LAUNCHER=ccache",
            "-DCMAKE_CXX_COMPILER_LAUNCHER=ccache",
            "-DBUILD_TESTING=ON",
        ]
    )

    # Adding platform specific options
    cmd += platform_options.get(PLATFORM, [])

    # Adding manylinux Python executables if --manylinux is set
    if manylinux:
        python_executables = (
            "/opt/python/cp38-cp38/bin/python;"
            "/opt/python/cp39-cp39/bin/python;"
            "/opt/python/cp310-cp310/bin/python;"
            "/opt/python/cp311-cp311/bin/python;"
            "/opt/python/cp312-cp312/bin/python;"
            "/opt/python/cp313-cp313/bin/python"
        )
        cmd.append(f"-DTHEROCK_DIST_PYTHON_EXECUTABLES={python_executables}")

    if PLATFORM == "windows":
        # VCToolsInstallDir is required for build. Throwing an error if environment variable doesn't exist
        if not vctools_install_dir:
            raise Exception(
                "Environment variable VCToolsInstallDir is not set. Please see https://github.com/ROCm/TheRock/blob/main/docs/development/windows_support.md#important-tool-settings about Windows tool configurations. Exiting."
            )

    # Splitting cmake options into an array (ex: "-flag X" -> ["-flag", "X"]) for subprocess.run
    cmake_options_arr = extra_cmake_options.split()
    cmd += cmake_options_arr

    logging.info(shlex.join(cmd))
    subprocess.run(cmd, cwd=THEROCK_DIR, check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run build configuration")
    parser.add_argument(
        "--manylinux",
        action="store_true",
        help="Enable manylinux build with multiple Python versions",
    )
    args = parser.parse_args()

    # Support both command-line flag and environment variable
    manylinux = args.manylinux or os.getenv("MANYLINUX") in ["1", "true"]

    build_configure(manylinux=manylinux)
