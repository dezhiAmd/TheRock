"""Tests for build_configure.py"""

import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

# Add parent directory to path so we can import build_configure
sys.path.insert(0, str(Path(__file__).parent.parent))

import build_configure

# Check if psutil is available
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


@contextmanager
def mock_psutil_resources(total_memory_gb, cpu_count_value):
    """
    Context manager to mock psutil resources.
    Uses different strategies based on whether psutil is installed.
    """
    if PSUTIL_AVAILABLE:
        # If psutil is installed, patch it normally
        with patch("psutil.virtual_memory") as mock_vm, \
             patch("psutil.cpu_count") as mock_cpu:
            mock_vm.return_value = MagicMock(total=total_memory_gb * 1024**3)
            mock_cpu.return_value = cpu_count_value
            yield
    else:
        # If psutil is not installed, inject a fake module
        mock_psutil = Mock()
        mock_psutil.virtual_memory.return_value = MagicMock(total=total_memory_gb * 1024**3)
        mock_psutil.cpu_count.return_value = cpu_count_value
        with patch.dict('sys.modules', {'psutil': mock_psutil}):
            yield


@contextmanager
def mock_psutil_exception(exception):
    """
    Context manager to mock psutil raising an exception.
    Uses different strategies based on whether psutil is installed.
    """
    if PSUTIL_AVAILABLE:
        # If psutil is installed, patch virtual_memory to raise exception
        with patch("psutil.virtual_memory", side_effect=exception):
            yield
    else:
        # If psutil is not installed, inject a fake module that raises exception
        mock_psutil = Mock()
        mock_psutil.virtual_memory.side_effect = exception
        with patch.dict('sys.modules', {'psutil': mock_psutil}):
            yield


class TestCalculateWindowsBackgroundJobs(unittest.TestCase):
    """Test the dynamic background jobs calculation for Windows"""

    def setUp(self):
        """Clean up environment before each test"""
        if "THEROCK_BACKGROUND_BUILD_JOBS" in os.environ:
            del os.environ["THEROCK_BACKGROUND_BUILD_JOBS"]

    def tearDown(self):
        """Clean up environment after each test"""
        if "THEROCK_BACKGROUND_BUILD_JOBS" in os.environ:
            del os.environ["THEROCK_BACKGROUND_BUILD_JOBS"]

    def test_environment_override(self):
        """Test that environment variable override works"""
        os.environ["THEROCK_BACKGROUND_BUILD_JOBS"] = "10"
        result = build_configure.calculate_windows_background_jobs()
        self.assertEqual(result, 10)

    def test_environment_override_invalid(self):
        """Test that invalid environment variable falls back to calculation"""
        os.environ["THEROCK_BACKGROUND_BUILD_JOBS"] = "invalid"
        
        with mock_psutil_resources(total_memory_gb=64, cpu_count_value=32):
            result = build_configure.calculate_windows_background_jobs()
            # Should fall back to calculation, not crash
            self.assertIsInstance(result, int)
            self.assertGreaterEqual(result, 2)

    def test_low_memory_system(self):
        """Test calculation on a low-memory system (16GB, 8 cores)"""
        with mock_psutil_resources(total_memory_gb=16, cpu_count_value=8):
            result = build_configure.calculate_windows_background_jobs()
            
            # (16GB - 8GB reserved) / 6GB per job = 1.33 -> 1
            # max(2, 8 cores / 20) = 2
            # min(1, 2) = 1, but enforced minimum is 2
            self.assertEqual(result, 2)

    def test_medium_memory_system(self):
        """Test calculation on a medium system (64GB, 32 cores)"""
        with mock_psutil_resources(total_memory_gb=64, cpu_count_value=32):
            result = build_configure.calculate_windows_background_jobs()
            
            # (64GB - 8GB) / 6GB = 9.33 -> 9
            # max(2, 32/20) = 2
            # min(9, 2) = 2
            self.assertEqual(result, 2)

    def test_high_memory_system(self):
        """Test calculation on a high-end system (256GB, 128 cores)"""
        with mock_psutil_resources(total_memory_gb=256, cpu_count_value=128):
            result = build_configure.calculate_windows_background_jobs()
            
            # (256GB - 8GB) / 6GB = 41.33 -> 41
            # max(2, 128/20) = 6
            # min(41, 6) = 6
            self.assertEqual(result, 6)

    def test_very_high_end_system(self):
        """Test calculation on a very high-end system (512GB, 256 cores)"""
        with mock_psutil_resources(total_memory_gb=512, cpu_count_value=256):
            result = build_configure.calculate_windows_background_jobs()
            
            # (512GB - 8GB) / 6GB = 84
            # max(2, 256/20) = 12
            # min(84, 12) = 12, but enforced maximum is 8
            self.assertEqual(result, 8)

    def test_cpu_bottleneck(self):
        """Test system where CPU is the limiting factor"""
        with mock_psutil_resources(total_memory_gb=128, cpu_count_value=40):
            result = build_configure.calculate_windows_background_jobs()
            
            # (128GB - 8GB) / 6GB = 20
            # max(2, 40/20) = 2
            # min(20, 2) = 2
            self.assertEqual(result, 2)

    def test_memory_bottleneck(self):
        """Test system where memory is the limiting factor"""
        # Hypothetical: 32GB RAM with 200 cores
        with mock_psutil_resources(total_memory_gb=32, cpu_count_value=200):
            result = build_configure.calculate_windows_background_jobs()
            
            # (32GB - 8GB) / 6GB = 4
            # max(2, 200/20) = 10
            # min(4, 10) = 4
            self.assertEqual(result, 4)

    @patch("builtins.__import__", side_effect=ImportError("psutil not available"))
    def test_psutil_not_available(self, mock_import):
        """Test fallback when psutil is not available"""
        result = build_configure.calculate_windows_background_jobs()
        self.assertEqual(result, 4)  # Should fall back to default

    def test_exception_during_calculation(self):
        """Test fallback when exception occurs during calculation"""
        with mock_psutil_exception(Exception("Something went wrong")):
            result = build_configure.calculate_windows_background_jobs()
            self.assertEqual(result, 4)  # Should fall back to default

    def test_minimum_bound_enforced(self):
        """Test that minimum of 2 jobs is enforced"""
        # Extreme low-end: 8GB RAM, 4 cores
        with mock_psutil_resources(total_memory_gb=8, cpu_count_value=4):
            result = build_configure.calculate_windows_background_jobs()
            
            # (8GB - 8GB) / 6GB = 0
            # max(2, 4/20) = 2
            # min(0, 2) = 0, but enforced minimum is 2
            self.assertGreaterEqual(result, 2)

    def test_maximum_bound_enforced(self):
        """Test that maximum of 8 jobs is enforced"""
        # Extreme high-end: 1TB RAM, 512 cores
        with mock_psutil_resources(total_memory_gb=1024, cpu_count_value=512):
            result = build_configure.calculate_windows_background_jobs()
            
            # Should be capped at 8
            self.assertLessEqual(result, 8)


class TestGetPlatformOptions(unittest.TestCase):
    """Test platform-specific options"""

    @patch("build_configure.PLATFORM", "windows")
    @patch("build_configure.vctools_install_dir", "C:/VC/Tools")
    def test_windows_platform_options(self):
        """Test that Windows platform options include dynamic background jobs"""
        # Mock a system that will result in 5 background jobs
        # We need to reverse engineer the calculation
        # With 64GB RAM and 100 cores: (64-8)/6=9, max(2,100/20)=5, min(9,5)=5
        with mock_psutil_resources(total_memory_gb=64, cpu_count_value=100):
            options = build_configure.get_platform_options()
            
            self.assertIsInstance(options, list)
            self.assertTrue(
                any("-DTHEROCK_BACKGROUND_BUILD_JOBS=5" in opt for opt in options),
                f"Expected background jobs option in {options}"
            )
            self.assertTrue(
                any("CMAKE_C_COMPILER" in opt for opt in options),
                f"Expected compiler option in {options}"
            )

    @patch("build_configure.PLATFORM", "linux")
    def test_linux_platform_options(self):
        """Test that Linux returns empty list (handled elsewhere)"""
        options = build_configure.get_platform_options()
        self.assertEqual(options, [])


if __name__ == "__main__":
    unittest.main()

