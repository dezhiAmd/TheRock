# GPU Memory Monitoring for CI Tests

This document describes the GPU memory monitoring system integrated into TheRock CI tests to help identify out-of-memory (OOM) failures.

## Overview

The GPU memory monitoring system automatically tracks GPU memory usage during test execution:

- **Per-GPU Memory**: Total, used, and free memory for each GPU adapter
- **Per-Process Memory**: Memory allocation by individual processes
- **Peak Usage Tracking**: Maximum memory usage reached during tests
- **Cross-Platform**: Supports both Linux (AMD GPUs) and Windows (all vendors)

## How It Works

### In CI Workflows

The monitoring is automatically integrated into `test_component.yml`:

1. **Before Tests**: GPU monitor starts in background (every 5 seconds by default)
2. **During Tests**: Monitor continuously samples GPU memory usage
3. **After Tests**: Monitor stops and generates summary report
4. **Artifact Upload**: All logs are uploaded as GitHub Actions artifacts

### Artifacts Generated

After each test run, the following artifacts are available:

- `gpu_memory_summary.log` - Human-readable summary with peak usage
- `gpu_memory_detail_<timestamp>.csv` - Detailed timestamped samples
- **Linux**: Single CSV with per-GPU metrics
- **Windows**: Two CSVs (per-adapter and per-process)

### Artifact Naming

Artifacts are named: `gpu-memory-logs-<component>-shard-<N>`

For example: `gpu-memory-logs-{"job_name":"mlir_tests"}-shard-1`

## Analyzing GPU Memory Issues

### 1. Download the Artifacts

In GitHub Actions:
1. Go to the failed workflow run
2. Scroll to "Artifacts" section at the bottom
3. Download `gpu-memory-logs-*` for the failed shard

### 2. Check the Summary

Look at `gpu_memory_summary.log`:

```
2026-01-16T10:30:15 - Starting GPU memory monitoring (interval: 5s)
2026-01-16T10:45:23 - Monitoring stopped.

=== Peak GPU Memory Usage ===
  GPU0: 15234.56 MB
  GPU1: 14892.32 MB
```

### 3. Analyze Detailed Logs

#### Linux Format (`gpu_memory_detail_*.csv`):
```csv
timestamp,gpu_id,used_mb,free_mb,total_mb,utilization_pct
2026-01-16T10:30:15,GPU0,1024.00,15360.00,16384.00,6.25
2026-01-16T10:30:20,GPU0,8192.00,8192.00,16384.00,50.00
2026-01-16T10:30:25,GPU0,16000.00,384.00,16384.00,97.66  <-- Near OOM!
```

#### Windows Format:

**Per-Adapter** (`gpu_per_adapter.csv`):
```csv
timestamp,adapter,dedicated_used_mb,dedicated_limit_mb,dedicated_free_mb,shared_used_mb,shared_limit_mb
2026-01-16T10:30:15,"AMD Radeon RX 7900 XTX",1024.00,24576.00,23552.00,512.00,32768.00
```

**Per-Process** (`gpu_per_process.csv`):
```csv
timestamp,pid,process_name,gpu_instance,dedicated_used_mb,shared_used_mb
2026-01-16T10:30:15,12345,"test_runner.exe","GPU 0",8192.00,256.00
2026-01-16T10:30:15,12346,"python.exe","GPU 0",512.00,128.00
```

### 4. Identify OOM Patterns

Look for:

- **Sudden spikes**: Memory jumps dramatically before crash
- **Gradual growth**: Memory leak pattern (steadily increasing)
- **Near-limit usage**: Used memory approaching total capacity (>95%)
- **Multiple processes**: Unexpected processes consuming GPU memory

### 5. Correlate with Test Logs

Match timestamps from GPU logs with test execution logs to identify:
- Which specific test was running when OOM occurred
- Whether certain test patterns trigger high memory usage
- If memory is properly freed between tests

## Manual Usage

You can also run the monitor manually for local debugging:

### Linux:
```bash
# Start monitoring
python build_tools/monitor_gpu_memory.py \
  --output-dir ./gpu_logs \
  --interval 5 &

MONITOR_PID=$!

# Run your tests
./run_tests.sh

# Stop monitoring
kill $MONITOR_PID

# Check results
cat ./gpu_logs/gpu_memory_summary.log
```

### Windows (PowerShell):
```powershell
# Start monitoring
$job = Start-Job -ScriptBlock {
  python build_tools/monitor_gpu_memory.py `
    --output-dir .\gpu_logs `
    --interval 5
}

# Run your tests
.\run_tests.ps1

# Stop monitoring
Stop-Job $job
Remove-Job $job

# Check results
Get-Content .\gpu_logs\gpu_memory_summary.log
```

## Configuration Options

### Sampling Interval

Default is 5 seconds. To change in workflow, edit `.github/workflows/test_component.yml`:

```yaml
python ./build_tools/monitor_gpu_memory.py \
  --output-dir "$GPU_LOG_DIR" \
  --interval 2  # Sample every 2 seconds
```

**Trade-offs**:
- Lower interval (1-2s): More granular data, higher overhead
- Higher interval (10-30s): Less overhead, might miss spikes

### Artifact Retention

Default is 7 days. To change:

```yaml
- name: Upload GPU memory logs
  uses: actions/upload-artifact@v4
  with:
    retention-days: 14  # Keep for 14 days
```

## Troubleshooting

### Monitor Fails to Start (Linux)

**Issue**: Neither `rocm-smi` nor `amd-smi` found

**Solution**: Ensure ROCm is installed and `THEROCK_BIN_DIR` is set correctly

```bash
# Check if tools are available
which rocm-smi
which amd-smi
echo $THEROCK_BIN_DIR
```

### Monitor Fails to Start (Windows)

**Issue**: PowerShell counters not available

**Solution**: Verify GPU drivers are installed and counters exist:

```powershell
Get-Counter '\GPU Adapter Memory(*)\Dedicated Usage' -ErrorAction SilentlyContinue
```

### No Artifacts Uploaded

**Issue**: Monitoring step was skipped or failed silently

**Check**:
1. Look for "Start GPU memory monitoring" step in workflow logs
2. Verify the monitor PID was captured
3. Check if background process started successfully

### Empty or Minimal Data

**Issue**: Monitor started but didn't collect samples

**Possible causes**:
- Test completed too quickly (< 5 seconds)
- Monitor process was killed prematurely
- GPU counters not accessible due to permissions

## Implementation Details

### Linux Implementation

Uses `rocm-smi` or `amd-smi` CLI tools to query:
- VRAM usage per GPU
- Memory utilization percentages
- Available/total memory

Falls back from `amd-smi` to `rocm-smi` for older ROCm versions.

### Windows Implementation

Uses PowerShell Performance Counters:
- `\GPU Adapter Memory(*)\Dedicated Usage` - Per-GPU dedicated memory
- `\GPU Adapter Memory(*)\Dedicated Limit` - Total dedicated memory
- `\GPU Process Memory(*)\Dedicated Usage` - Per-process allocation
- `\GPU Process Memory(*)\Shared Usage` - Shared memory usage

Vendor-agnostic (works with AMD, NVIDIA, Intel).

### Background Execution

The monitor runs as a background process using:
- Linux: `python script.py &` with captured PID
- Signal handling: Graceful SIGTERM, then SIGKILL if needed
- Cleanup: Always runs even if tests fail (`if: always()`)

## Future Enhancements

Potential improvements:

1. **Alerting**: Automatic warnings when memory usage exceeds thresholds
2. **Visualization**: Generate charts from CSV data
3. **Integration**: Parse logs in test reporting tools
4. **Memory Diff**: Compare memory usage across test runs
5. **Process Correlation**: Link processes to specific test cases

## Related Files

- `build_tools/monitor_gpu_memory.py` - Main monitoring script
- `.github/workflows/test_component.yml` - Workflow integration
- `build_tools/print_driver_gpu_info.py` - Initial GPU sanity check
- `build_tools/health_status.py` - Pre-build diagnostics

## Support

For issues or questions about GPU memory monitoring:
1. Check workflow logs for monitor startup/shutdown messages
2. Download and inspect the GPU memory artifacts
3. Verify GPU tools are accessible in the test environment
4. File an issue with logs and artifact data attached
