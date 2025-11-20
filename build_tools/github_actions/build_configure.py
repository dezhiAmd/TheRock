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
  - THEROCK_BACKGROUND_BUILD_JOBS (override automatic calculation)
"""

import argparse
import logging
import os
from pathlib import Path
import platform
import shlex
import subprocess

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


def calculate_windows_background_jobs():
    """
    Calculate optimal number of background build jobs for Windows based on
    system resources (memory and CPU cores).

    Strategy:
    1. Memory-based limit: Estimate each background job needs ~6GB of RAM
    2. CPU-based limit: Similar to Linux (cores / 20, minimum 2)
    3. Return the minimum of both to avoid resource exhaustion
    4. Respect THEROCK_BACKGROUND_BUILD_JOBS environment variable override

    Returns:
        int: Number of background build jobs to use
    """
    # Check for environment variable override first
    env_override = os.getenv("THEROCK_BACKGROUND_BUILD_JOBS")
    if env_override:
        try:
            jobs = int(env_override)
            if jobs > 0:
                logging.info(
                    f"Using THEROCK_BACKGROUND_BUILD_JOBS from environment: {jobs}"
                )
                return jobs
        except ValueError:
            logging.warning(
                f"Invalid THEROCK_BACKGROUND_BUILD_JOBS value: {env_override}, "
                "falling back to automatic calculation"
            )

    try:
        import psutil

        # Get system memory in GB
        total_memory_gb = psutil.virtual_memory().total / (1024**3)

        # Get CPU core count
        cpu_count = psutil.cpu_count(logical=False) or psutil.cpu_count()

        # Memory-based calculation
        # Conservative estimate: each background job can use 6GB peak memory
        # Reserve 8GB for the system and foreground builds
        GB_PER_BACKGROUND_JOB = 6
        RESERVED_MEMORY_GB = 8
        available_for_background = max(0, total_memory_gb - RESERVED_MEMORY_GB)
        memory_based_jobs = int(available_for_background / GB_PER_BACKGROUND_JOB)

        # CPU-based calculation (similar to Linux logic in therock_job_pools.cmake)
        # Use cores / 20, with a minimum of 2
        cpu_based_jobs = max(2, cpu_count // 20)

        # Take the minimum to avoid overwhelming either resource
        # But enforce absolute minimum of 2 and maximum of 8 for safety
        calculated_jobs = max(2, min(8, min(memory_based_jobs, cpu_based_jobs)))

        logging.info(
            f"Windows background jobs calculation:\n"
            f"  Total Memory: {total_memory_gb:.1f} GB\n"
            f"  CPU Cores: {cpu_count}\n"
            f"  Memory-based limit: {memory_based_jobs} jobs "
            f"({available_for_background:.1f} GB / {GB_PER_BACKGROUND_JOB} GB per job)\n"
            f"  CPU-based limit: {cpu_based_jobs} jobs "
            f"({cpu_count} cores / 20)\n"
            f"  Final calculated: {calculated_jobs} background jobs"
        )

        return calculated_jobs

    except ImportError:
        logging.warning(
            "psutil not available, falling back to default of 4 background jobs. "
            "Install psutil for dynamic resource-based calculation."
        )
        return 4
    except Exception as e:
        logging.warning(
            f"Error calculating background jobs: {e}, "
            "falling back to default of 4"
        )
        return 4


def get_platform_options():
    """Get platform-specific CMake options."""
    if PLATFORM == "windows":
        background_jobs = calculate_windows_background_jobs()
        return [
            f"-DCMAKE_C_COMPILER={vctools_install_dir}/bin/Hostx64/x64/cl.exe",
            f"-DCMAKE_CXX_COMPILER={vctools_install_dir}/bin/Hostx64/x64/cl.exe",
            f"-DCMAKE_LINKER={vctools_install_dir}/bin/Hostx64/x64/link.exe",
            f"-DTHEROCK_BACKGROUND_BUILD_JOBS={background_jobs}",
        ]
    return []


platform_options = {
    "windows": get_platform_options() if PLATFORM == "windows" else [],
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
