import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.fspath(Path(__file__).parent.parent))
from build_configure import calculate_background_build_jobs


class BuildConfigureTest(unittest.TestCase):
    def setUp(self):
        # Save environment state
        self._saved_env = {}
        key = "THEROCK_BACKGROUND_BUILD_JOBS_CALCULATED"
        if key in os.environ:
            self._saved_env[key] = os.environ[key]
            del os.environ[key]

    def tearDown(self):
        # Restore environment state
        key = "THEROCK_BACKGROUND_BUILD_JOBS_CALCULATED"
        if key in os.environ:
            del os.environ[key]
        if key in self._saved_env:
            os.environ[key] = self._saved_env[key]

    @patch('build_configure.psutil')
    @patch('build_configure.multiprocessing')
    def test_calculate_background_jobs_cpu_constrained(self, mock_multiprocessing, mock_psutil):
        """Test when CPU is the limiting factor (low core count, high memory)"""
        # Mock 40 CPU cores and 128 GB memory
        mock_multiprocessing.cpu_count.return_value = 40
        mock_memory = MagicMock()
        mock_memory.total = 128 * (1024 ** 3)  # 128 GB
        mock_memory.available = 100 * (1024 ** 3)  # 100 GB available
        mock_psutil.virtual_memory.return_value = mock_memory

        # Expected: 40 / 20 = 2 (CPU constraint)
        # Memory would allow: 100 / 2 = 50
        # Min of both: 2
        result = calculate_background_build_jobs()
        self.assertEqual(result, 2)

    @patch('build_configure.psutil')
    @patch('build_configure.multiprocessing')
    def test_calculate_background_jobs_memory_constrained(self, mock_multiprocessing, mock_psutil):
        """Test when memory is the limiting factor (high core count, low memory)"""
        # Mock 200 CPU cores and 16 GB memory
        mock_multiprocessing.cpu_count.return_value = 200
        mock_memory = MagicMock()
        mock_memory.total = 16 * (1024 ** 3)  # 16 GB
        mock_memory.available = 10 * (1024 ** 3)  # 10 GB available
        mock_psutil.virtual_memory.return_value = mock_memory

        # Expected: 200 / 20 = 10 (CPU would allow)
        # Memory allows: 10 / 2 = 5 (Memory constraint)
        # Min of both: 5
        result = calculate_background_build_jobs()
        self.assertEqual(result, 5)

    @patch('build_configure.psutil')
    @patch('build_configure.multiprocessing')
    def test_calculate_background_jobs_capped_at_8(self, mock_multiprocessing, mock_psutil):
        """Test that result is capped at 8 even with high resources"""
        # Mock 500 CPU cores and 256 GB memory
        mock_multiprocessing.cpu_count.return_value = 500
        mock_memory = MagicMock()
        mock_memory.total = 256 * (1024 ** 3)  # 256 GB
        mock_memory.available = 200 * (1024 ** 3)  # 200 GB available
        mock_psutil.virtual_memory.return_value = mock_memory

        # Expected: 500 / 20 = 25 (CPU would allow)
        # Memory allows: 200 / 2 = 100 (Memory would allow)
        # Min of both: 25, but capped at 8
        result = calculate_background_build_jobs()
        self.assertEqual(result, 8)

    @patch('build_configure.psutil')
    @patch('build_configure.multiprocessing')
    def test_calculate_background_jobs_minimum_2(self, mock_multiprocessing, mock_psutil):
        """Test that result is at least 2 even with very low resources"""
        # Mock 4 CPU cores and 2 GB memory
        mock_multiprocessing.cpu_count.return_value = 4
        mock_memory = MagicMock()
        mock_memory.total = 2 * (1024 ** 3)  # 2 GB
        mock_memory.available = 1 * (1024 ** 3)  # 1 GB available
        mock_psutil.virtual_memory.return_value = mock_memory

        # Expected: 4 / 20 = 0 -> max(2, 0) = 2 (CPU minimum)
        # Memory allows: 1 / 2 = 0 -> max(2, 0) = 2 (Memory minimum)
        # Min of both: 2
        # Final: 2 (minimum enforced)
        result = calculate_background_build_jobs()
        self.assertEqual(result, 2)

    @patch('build_configure.psutil')
    @patch('build_configure.multiprocessing')
    def test_calculate_background_jobs_cached(self, mock_multiprocessing, mock_psutil):
        """Test that cached value is reused"""
        # Set cached value
        os.environ["THEROCK_BACKGROUND_BUILD_JOBS_CALCULATED"] = "6"

        # Call function - should return cached value without calling psutil/multiprocessing
        result = calculate_background_build_jobs()
        self.assertEqual(result, 6)

        # Verify that psutil and multiprocessing were NOT called
        mock_multiprocessing.cpu_count.assert_not_called()
        mock_psutil.virtual_memory.assert_not_called()

    @patch('build_configure.psutil')
    @patch('build_configure.multiprocessing')
    def test_calculate_background_jobs_typical_ci_runner(self, mock_multiprocessing, mock_psutil):
        """Test with typical CI runner specs (e.g., 8 cores, 32 GB RAM)"""
        # Mock typical CI runner: 8 CPU cores and 32 GB memory
        mock_multiprocessing.cpu_count.return_value = 8
        mock_memory = MagicMock()
        mock_memory.total = 32 * (1024 ** 3)  # 32 GB
        mock_memory.available = 28 * (1024 ** 3)  # 28 GB available
        mock_psutil.virtual_memory.return_value = mock_memory

        # Expected: 8 / 20 = 0 -> max(2, 0) = 2 (CPU)
        # Memory allows: 28 / 2 = 14 (Memory)
        # Min of both: 2
        result = calculate_background_build_jobs()
        self.assertEqual(result, 2)

    @patch('build_configure.psutil')
    @patch('build_configure.multiprocessing')
    def test_calculate_background_jobs_sets_env_variable(self, mock_multiprocessing, mock_psutil):
        """Test that the function sets the environment variable for debugging"""
        # Mock resources
        mock_multiprocessing.cpu_count.return_value = 80
        mock_memory = MagicMock()
        mock_memory.total = 64 * (1024 ** 3)  # 64 GB
        mock_memory.available = 50 * (1024 ** 3)  # 50 GB available
        mock_psutil.virtual_memory.return_value = mock_memory

        # Call function
        result = calculate_background_build_jobs()

        # Verify environment variable is set
        self.assertIn("THEROCK_BACKGROUND_BUILD_JOBS_CALCULATED", os.environ)
        self.assertEqual(os.environ["THEROCK_BACKGROUND_BUILD_JOBS_CALCULATED"], str(result))


if __name__ == "__main__":
    unittest.main()

