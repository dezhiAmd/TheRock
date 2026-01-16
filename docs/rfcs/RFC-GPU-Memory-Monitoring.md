# RFC: GPU Memory Monitoring for CI Test Debugging

**Status**: Proposed  
**Author**: CI/CD Team  
**Created**: 2026-01-16  
**Updated**: 2026-01-16  

---

## Executive Summary

This RFC proposes implementing automated GPU memory monitoring during CI test execution to help diagnose test failures caused by insufficient GPU memory (out-of-memory/OOM errors). The system will continuously track GPU memory usage, generate timestamped logs, and upload them as artifacts, enabling engineers to quickly identify whether a test failure was caused by GPU memory exhaustion.

---

## Table of Contents

1. [Problem Statement](#problem-statement)
2. [Motivation](#motivation)
3. [Goals and Non-Goals](#goals-and-non-goals)
4. [Background](#background)
5. [Proposed Solution](#proposed-solution)
6. [Detailed Design](#detailed-design)
7. [Implementation](#implementation)
8. [Alternatives Considered](#alternatives-considered)
9. [Security and Privacy Considerations](#security-and-privacy-considerations)
10. [Performance Impact](#performance-impact)
11. [Testing Strategy](#testing-strategy)
12. [Rollout Plan](#rollout-plan)
13. [Success Metrics](#success-metrics)
14. [Open Questions](#open-questions)
15. [References](#references)

---

## Problem Statement

### Current Situation

When CI tests fail on GPU-enabled runners, engineers face significant challenges determining if the failure was caused by:

1. **GPU Out-of-Memory (OOM) errors** - Insufficient VRAM for the test workload
2. **Logic errors** - Bugs in the code being tested
3. **Infrastructure issues** - Corrupted drivers, hardware problems
4. **Resource contention** - Multiple processes competing for GPU memory

**The core problem**: Without GPU memory telemetry, engineers must:
- Re-run tests locally (time-consuming, may not reproduce)
- Add instrumentation code to tests (intrusive, requires code changes)
- Make educated guesses based on error messages (unreliable)
- Request manual investigation by infrastructure team (slow, resource-intensive)

### Impact

This lack of visibility results in:
- **Increased debugging time**: 2-4 hours average to diagnose OOM vs logic errors
- **False failure classifications**: OOM failures incorrectly attributed to code bugs
- **Wasted CI resources**: Re-running tests that will fail again due to insufficient memory
- **Developer frustration**: Uncertainty about whether local fixes will resolve CI failures
- **Delayed releases**: Unable to confidently merge PRs with intermittent test failures

### Example Scenario

```
Test Failure Log:
  RuntimeError: HIP error: out of resources
  
Questions engineers cannot answer:
  - Was GPU memory actually exhausted?
  - Which GPU ran out of memory?
  - What was the peak usage vs. available memory?
  - Did memory gradually increase (leak) or spike suddenly?
  - Was there memory fragmentation?
  - Were other processes consuming GPU memory?
```

---

## Motivation

### Business Impact

1. **Faster debugging**: Reduce time-to-diagnosis from hours to minutes
2. **Better resource planning**: Identify which tests need more GPU memory
3. **Improved reliability**: Distinguish transient OOM from persistent bugs
4. **Cost optimization**: Avoid unnecessary test reruns and investigations
5. **Data-driven decisions**: Use memory metrics to optimize test workloads

### Technical Benefits

1. **Root cause analysis**: Clear evidence whether OOM caused the failure
2. **Trend analysis**: Track memory usage patterns across commits
3. **Capacity planning**: Identify when to upgrade runner GPU memory
4. **Test optimization**: Find memory-inefficient tests for refactoring
5. **Prevention**: Set memory thresholds to fail tests before OOM crashes

### User Stories

**As a test engineer**, I want to see GPU memory usage during CI runs so that I can determine if a test failure was caused by OOM.

**As a CI infrastructure engineer**, I want historical GPU memory data so that I can right-size runner GPU configurations.

**As a release manager**, I want to identify memory-related test instabilities so that I can make informed merge decisions.

---

## Goals and Non-Goals

### Goals

✅ **Automated monitoring**: Run continuously during all CI test jobs without manual intervention  
✅ **Cross-platform**: Support Linux (AMD) and Windows (AMD/NVIDIA/Intel) runners  
✅ **Low overhead**: Minimal performance impact (<1% CPU, <5 second sampling)  
✅ **Always available**: Capture logs even when tests crash or timeout  
✅ **Easy analysis**: Human-readable summaries + CSV for programmatic analysis  
✅ **Actionable data**: Provide enough detail to make debugging decisions  

### Non-Goals

❌ **Real-time alerting**: Not implementing Slack/email notifications (future work)  
❌ **Historical database**: Not storing metrics in a time-series DB (use existing artifacts)  
❌ **Memory profiling**: Not doing allocation-level profiling (too invasive)  
❌ **Automatic remediation**: Not automatically retrying with more memory  
❌ **Test modification**: Not requiring changes to existing test code  
❌ **Production monitoring**: Only for CI environments, not production workloads  

### Success Criteria

This RFC is successful if, within 30 days of deployment:

1. ✓ Engineers can diagnose ≥80% of GPU memory issues within 10 minutes
2. ✓ Average debugging time for OOM failures decreases by ≥50%
3. ✓ Zero false negatives (all actual OOM events are captured)
4. ✓ Test execution time overhead is <2%
5. ✓ Artifact storage increase is <100MB per test shard

---

## Background

### GPU Memory Architecture

**Linux (AMD GPUs)**:
- **VRAM**: Dedicated high-bandwidth memory on GPU (e.g., 16GB, 24GB, 48GB)
- **Managed via**: ROCm runtime, exposed through `rocm-smi` / `amd-smi`
- **Key metrics**: Used, Free, Total VRAM per GPU

**Windows (Multi-vendor)**:
- **Dedicated memory**: GPU-local VRAM (similar to Linux)
- **Shared memory**: System RAM accessible by GPU
- **Managed via**: Windows Performance Counters (vendor-agnostic)
- **Key metrics**: Dedicated/Shared usage per adapter and per process

### Existing Tools

| Tool | Platform | Vendor | Limitations |
|------|----------|--------|-------------|
| `rocm-smi` | Linux | AMD | Manual invocation, not timestamped |
| `amd-smi` | Linux | AMD | Newer, but not all runners have it |
| `nvidia-smi` | Linux/Win | NVIDIA | Not available on AMD runners |
| Windows Perf Counters | Windows | All | Requires PowerShell scripting |
| HIP Runtime APIs | Both | AMD | Requires code instrumentation |

**Gap**: No automated, continuous, cross-platform monitoring integrated into CI.

### Current Test Infrastructure

```yaml
Test Job Flow:
  1. Setup environment
  2. Checkout code
  3. Download artifacts
  4. Health checks (CPU, disk, Python)
  5. Driver/GPU sanity check (one-time)
  6. Run tests ← NO MONITORING HERE
  7. Upload results
  8. Cleanup
```

The driver/GPU sanity check (step 5) only verifies GPU accessibility, not ongoing memory usage.

---

## Proposed Solution

### High-Level Design

Implement a **lightweight background monitoring process** that:

1. **Starts before tests run**: Launch monitor as background process
2. **Samples periodically**: Every 5 seconds (configurable)
3. **Logs to CSV files**: Timestamped, structured data
4. **Stops after tests**: Graceful shutdown with summary generation
5. **Uploads artifacts**: Always runs, even on test failure

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────┐
│                    GitHub Actions Runner                 │
│                                                          │
│  ┌────────────────────────────────────────────────────┐ │
│  │              test_component.yml workflow            │ │
│  │                                                      │ │
│  │  1. [Setup] ────────────────────────────────────┐  │ │
│  │                                                  │  │ │
│  │  2. [Health checks]                             │  │ │
│  │                                                  │  │ │
│  │  3. [GPU sanity check]                          │  │ │
│  │                                                  │  │ │
│  │  4. START GPU Monitor ──┐                       │  │ │
│  │           │              │                       │  │ │
│  │           ├─(PID)────────┤                       │  │ │
│  │           │              │                       │  │ │
│  │           ▼              │                       │  │ │
│  │  ┌─────────────────┐    │                       │  │ │
│  │  │ Background      │    │                       │  │ │
│  │  │ Monitor Process │    │                       │  │ │
│  │  │                 │    │                       │  │ │
│  │  │ Every 5s:       │    │                       │  │ │
│  │  │ • Query GPU     │◄───┼─ GPU Adapter/Driver  │  │ │
│  │  │ • Write CSV     │    │                       │  │ │
│  │  │ • Track peaks   │    │                       │  │ │
│  │  └─────────────────┘    │                       │  │ │
│  │           │              │                       │  │ │
│  │  5. [Run Tests] ◄────────┘  ← Tests execute     │  │ │
│  │                              normally            │  │ │
│  │  6. STOP Monitor (send SIGTERM)                 │  │ │
│  │           │                                      │  │ │
│  │           ▼                                      │  │ │
│  │     Generate summary.log                        │  │ │
│  │                                                  │  │ │
│  │  7. [Upload artifacts: gpu_memory_logs/]        │  │ │
│  │                                                  │  │ │
│  │  8. [Cleanup]                                    │  │ │
│  └────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

### Data Flow

```
GPU Hardware
    │
    ├─ Linux: rocm-smi/amd-smi CLI
    │     └─> Parse stdout → CSV
    │
    └─ Windows: Performance Counters
          └─> PowerShell script → CSV

CSV Files (per test run):
  • gpu_memory_detail_<timestamp>.csv    (time-series data)
  • gpu_memory_summary.log              (human-readable)
  • gpu_per_adapter.csv (Windows only)
  • gpu_per_process.csv (Windows only)

GitHub Artifacts:
  • gpu-memory-logs-<component>-shard-<N>.zip
  • Retention: 7 days
  • Downloadable via Actions UI
```

---

## Detailed Design

### Component 1: Python Monitoring Script

**File**: `build_tools/monitor_gpu_memory.py`

```python
class GPUMemoryMonitor:
    """Base class with platform-specific implementations."""
    
    def __init__(self, output_dir, interval=5):
        # Setup output directory and log files
        # Register signal handlers for graceful shutdown
        
    def start(self):
        # Main loop: sample → log → sleep
        # Handle signals and exceptions
        
    def collect_sample(self, sample_num):
        # Platform-specific: query GPU, write CSV
        
    def finalize(self):
        # Write summary with peak usage
```

**Key Design Decisions**:

1. **Python implementation**: Consistent with existing build tools
2. **Class hierarchy**: `LinuxGPUMonitor` and `WindowsGPUMonitor` inherit from base
3. **Background execution**: Use `&` in shell, capture PID for cleanup
4. **Signal handling**: SIGTERM for graceful shutdown, SIGKILL as fallback
5. **CSV format**: Standard, parseable by pandas/Excel/scripts

### Component 2: Workflow Integration

**File**: `.github/workflows/test_component.yml`

**New Steps**:

```yaml
- name: Start GPU memory monitoring
  id: gpu_monitor
  run: |
    python ./build_tools/monitor_gpu_memory.py \
      --output-dir $GPU_LOG_DIR --interval 5 &
    echo "monitor_pid=$!" >> $GITHUB_OUTPUT

- name: Test
  # ... existing test execution ...

- name: Stop GPU memory monitoring
  if: always()  # ← Critical: run even on test failure
  run: |
    kill -TERM ${{ steps.gpu_monitor.outputs.monitor_pid }}
    cat $GPU_LOG_DIR/gpu_memory_summary.log

- name: Upload GPU memory logs
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: gpu-memory-logs-${{ inputs.component }}-shard-${{ matrix.shard }}
    path: ${{ steps.gpu_monitor.outputs.gpu_log_dir }}
```

**Design Rationale**:

- `if: always()`: Ensures logs are captured even if tests crash/timeout
- `id: gpu_monitor`: Allows subsequent steps to reference outputs
- `$GITHUB_OUTPUT`: Stores PID and log directory for cleanup step
- `actions/upload-artifact@v4`: Uses latest stable artifact upload action

### Component 3: Linux Implementation

**Data Source**: `rocm-smi` or `amd-smi` CLI tools

**Sample Command**:
```bash
$ rocm-smi --showmeminfo vram
GPU[0]: Memory (Used/Total): 8192 MB / 16384 MB
GPU[1]: Memory (Used/Total): 12345 MB / 16384 MB
```

**Parsing Strategy**:
1. Execute command via `subprocess.run()`
2. Parse stdout with regex: `GPU\[(\d+)\].*?(\d+)\s+MB\s+/\s+(\d+)\s+MB`
3. Extract: gpu_id, used_mb, total_mb
4. Calculate: free_mb, utilization_pct

**Fallback Logic**:
```python
# Try amd-smi first (newer)
if shutil.which("amd-smi"):
    use_amd_smi()
# Fall back to rocm-smi
elif shutil.which("rocm-smi"):
    use_rocm_smi()
else:
    raise RuntimeError("No SMI tool available")
```

### Component 4: Windows Implementation

**Data Source**: PowerShell Performance Counters

**Sample PowerShell**:
```powershell
Get-Counter '\GPU Adapter Memory(*)\Dedicated Usage'
Get-Counter '\GPU Adapter Memory(*)\Dedicated Limit'
Get-Counter '\GPU Process Memory(*)\Dedicated Usage'
```

**Architecture**:
1. Python creates temporary `.ps1` script
2. Python invokes `powershell.exe -File script.ps1`
3. PowerShell queries counters, outputs CSV
4. Python appends to log file
5. Cleanup: Delete `.ps1` on exit

**Advantages**:
- Vendor-agnostic (works with AMD, NVIDIA, Intel)
- No external dependencies
- Per-process breakdown available

**Disadvantages**:
- Slower than native APIs (~200ms per sample)
- Requires PowerShell (already available on Windows runners)

### Data Schema

**Per-GPU CSV** (Linux):
```csv
timestamp,gpu_id,used_mb,free_mb,total_mb,utilization_pct
2026-01-16T10:30:15.123Z,GPU0,1024.00,15360.00,16384.00,6.25
2026-01-16T10:30:20.456Z,GPU0,8192.00,8192.00,16384.00,50.00
```

**Per-Adapter CSV** (Windows):
```csv
timestamp,adapter,dedicated_used_mb,dedicated_limit_mb,dedicated_free_mb,shared_used_mb,shared_limit_mb
2026-01-16T10:30:15.123Z,"AMD Radeon RX 7900 XTX",1024.00,24576.00,23552.00,512.00,32768.00
```

**Per-Process CSV** (Windows):
```csv
timestamp,pid,process_name,gpu_instance,dedicated_used_mb,shared_used_mb
2026-01-16T10:30:15.123Z,12345,"python.exe","GPU 0",2048.00,256.00
```

**Summary Log** (Both):
```
2026-01-16T10:30:15 - Starting GPU memory monitoring (interval: 5s)
2026-01-16T10:30:15 - Detail log: /path/to/gpu_memory_detail_20260116_103015.csv
2026-01-16T10:30:15 - Summary log: /path/to/gpu_memory_summary.log
2026-01-16T10:45:30 - Received signal 15, stopping monitoring...
2026-01-16T10:45:30 - Monitoring stopped.

=== Peak GPU Memory Usage ===
  GPU0: 15234.56 MB
  GPU1: 14892.32 MB
```

---

## Implementation

### Phase 1: Core Implementation (Week 1)

**Tasks**:
- [ ] Implement `monitor_gpu_memory.py` base class
- [ ] Implement `LinuxGPUMonitor` with rocm-smi support
- [ ] Implement `WindowsGPUMonitor` with PowerShell counters
- [ ] Add command-line argument parsing
- [ ] Implement signal handling and cleanup
- [ ] Add CSV writing with proper escaping

**Deliverables**:
- Working Python script
- Unit tests for parsing logic
- Manual testing on Linux and Windows runners

### Phase 2: Workflow Integration (Week 1-2)

**Tasks**:
- [ ] Add "Start GPU memory monitoring" step to `test_component.yml`
- [ ] Add "Stop GPU memory monitoring" step with `if: always()`
- [ ] Add artifact upload step
- [ ] Test on small component (e.g., unit tests)
- [ ] Verify artifacts are uploaded on success and failure

**Deliverables**:
- Updated workflow file
- Successful test run with uploaded artifacts
- Documentation in workflow comments

### Phase 3: Documentation (Week 2)

**Tasks**:
- [ ] Create `GPU_MEMORY_MONITORING.md` user guide
- [ ] Add troubleshooting section
- [ ] Document analysis workflows (how to use the logs)
- [ ] Add example CSV snippets
- [ ] Create this RFC

**Deliverables**:
- Comprehensive documentation
- Examples of debugging real OOM failures

### Phase 4: Rollout (Week 2-3)

**Tasks**:
- [ ] Deploy to 10% of test jobs (canary)
- [ ] Monitor for failures and performance impact
- [ ] Deploy to 50% of test jobs
- [ ] Deploy to 100% of test jobs
- [ ] Announce availability to team

**Deliverables**:
- Monitoring enabled on all test jobs
- Team announcement with documentation links

### Phase 5: Iteration (Week 4+)

**Tasks**:
- [ ] Collect feedback from engineers
- [ ] Add amd-smi support for newer ROCm versions
- [ ] Optimize sampling interval based on overhead measurements
- [ ] Add configurable memory thresholds (future: fail tests at 95% usage)
- [ ] Explore real-time alerting (future work)

---

## Alternatives Considered

### Alternative 1: Instrumentation in Test Code

**Approach**: Add GPU memory logging calls inside test code.

```python
# In each test file
def test_my_feature():
    log_gpu_memory("before test")
    run_test()
    log_gpu_memory("after test")
```

**Pros**:
- More granular: memory usage per test function
- Can measure specific operations

**Cons**:
- ❌ **Intrusive**: Requires modifying thousands of test files
- ❌ **Maintenance burden**: Every new test needs instrumentation
- ❌ **Inconsistent**: Depends on developer discipline
- ❌ **Not helpful for crashes**: If test crashes, logging code doesn't run

**Decision**: Rejected - too invasive and maintenance-heavy.

### Alternative 2: Post-Hoc Analysis with Logs

**Approach**: Parse existing test logs for GPU error messages.

**Pros**:
- No new code needed
- Works with existing infrastructure

**Cons**:
- ❌ **Reactive**: Only detects errors after they occur
- ❌ **Incomplete**: Error messages don't show memory trends
- ❌ **Ambiguous**: "Out of resources" could mean many things
- ❌ **No baseline**: Can't see memory usage before failure

**Decision**: Rejected - insufficient information for debugging.

### Alternative 3: Always-On System-Level Monitoring

**Approach**: Deploy Prometheus + GPU exporters on all runners.

**Pros**:
- Comprehensive: Monitors all processes, not just tests
- Historical: Data retained in time-series database
- Dashboards: Real-time visualization

**Cons**:
- ❌ **Infrastructure complexity**: Requires Prometheus deployment
- ❌ **Cost**: Additional storage and compute for metrics DB
- ❌ **Overkill**: Most tests don't need this level of monitoring
- ❌ **Latency**: Data ingestion delay (30-60 seconds)

**Decision**: Rejected for initial implementation - can be future work for advanced users.

### Alternative 4: Vendor-Specific Tools Only

**Approach**: Use `rocm-smi` on Linux, `nvidia-smi` on Windows.

**Pros**:
- Native tools: More accurate and efficient
- Rich data: More metrics available

**Cons**:
- ❌ **Not cross-platform**: Different CLIs, output formats
- ❌ **Vendor lock-in**: Doesn't work on Intel/other GPUs
- ❌ **Maintenance**: Need to handle multiple tools

**Decision**: Partially adopted - use rocm-smi on Linux, but add Windows Performance Counters for cross-vendor support on Windows.

### Alternative 5: Sampling at Test Boundaries Only

**Approach**: Only measure GPU memory before/after each test, not during.

**Pros**:
- Lower overhead: No continuous sampling
- Simpler: Fewer data points to store

**Cons**:
- ❌ **Misses spikes**: Peak usage often occurs mid-test
- ❌ **Poor for leaks**: Can't see gradual memory growth
- ❌ **Loses context**: Don't know when OOM occurred

**Decision**: Rejected - continuous sampling is essential for root cause analysis.

---

## Security and Privacy Considerations

### Data Sensitivity

**What data is collected**:
- ✅ GPU memory usage (numeric values)
- ✅ GPU adapter names (e.g., "AMD Radeon RX 7900 XTX")
- ✅ Process IDs and names (e.g., "python.exe", "test_runner")
- ❌ NOT collected: Memory contents, test data, code

**Risk Level**: **LOW**

The data contains only system performance metrics, no sensitive information.

### Access Control

**Artifact Access**:
- Artifacts are stored in GitHub Actions
- Access restricted to: Repository collaborators with read access
- Same access level as existing test logs and results

**No additional permissions required**.

### Compliance

- **GDPR**: No personal data collected
- **Corporate policies**: Aligns with existing logging practices
- **Open source**: Safe to open-source the monitoring code

### Potential Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Process names leak sensitive info | Low | Low | Process names are already in system logs |
| GPU adapter names reveal hardware | Low | Low | Hardware info already visible in setup logs |
| Someone tampers with monitoring script | Low | Medium | Code review required for changes |
| Logs uploaded to public location | Very Low | Medium | Artifacts are private by default |

**Conclusion**: No significant security concerns. Standard code review process is sufficient.

---

## Performance Impact

### CPU Overhead

**Estimated**: <1% CPU utilization per monitoring process

**Breakdown**:
- Python interpreter: ~0.2% CPU
- Subprocess calls (rocm-smi/PowerShell): ~0.5% CPU per sample
- File I/O: ~0.1% CPU
- Total: ~0.8% CPU (sampled every 5 seconds)

**Measurement plan**: Compare test execution times with/without monitoring over 100 runs.

### Memory Overhead

**Estimated**: <50 MB RAM per monitoring process

**Breakdown**:
- Python process: ~30 MB
- Log buffers: ~5 MB
- PowerShell child processes (Windows): ~15 MB peak
- Total: ~50 MB

**Impact**: Negligible (runners have 32-64 GB RAM).

### Disk I/O

**Per 1-hour test run**:
- Samples: 720 (3600s / 5s)
- CSV row size: ~100 bytes
- Total: ~70 KB per test

**For 1000 tests/day**: ~70 MB/day

**Impact**: Negligible (local SSD, no network I/O during test).

### Network/Storage

**Artifact upload**:
- Compressed CSV: ~20-50 KB per test shard
- Upload time: <5 seconds
- Storage: 7-day retention

**For 1000 tests/day**: ~50 KB * 1000 = ~50 MB/day * 7 days = ~350 MB total

**Impact**: Minimal (GitHub provides 50 GB artifact storage).

### Test Execution Time Impact

**Expected**: <2% increase in total job time

**Breakdown**:
- Monitor startup: ~2 seconds
- During tests: ~0.8% CPU overhead → negligible wall-clock time
- Monitor shutdown: ~3 seconds
- Artifact upload: ~5 seconds
- **Total added time**: ~10 seconds per job

**For 30-minute test**: 10s / 1800s = 0.6% overhead ✅

**Measurement plan**: 
- Track P50, P95, P99 test durations before/after
- Alert if >2% regression observed

---

## Testing Strategy

### Unit Tests

**File**: `build_tools/tests/test_monitor_gpu_memory.py`

**Coverage**:
- [ ] Argument parsing
- [ ] CSV formatting and escaping
- [ ] Signal handling
- [ ] Peak tracking logic
- [ ] Linux SMI output parsing
- [ ] Windows PowerShell output parsing

**Mocking**:
- Mock `subprocess.run()` to return sample GPU data
- Mock `signal.signal()` to test handler registration
- Mock file I/O to test CSV writing

### Integration Tests

**Manual testing**:
1. Run on Linux runner with AMD GPU
   - Verify rocm-smi is called
   - Check CSV format
   - Confirm peak tracking works
2. Run on Windows runner with GPU
   - Verify PowerShell script is created and executed
   - Check both adapter and process CSVs
   - Confirm cleanup (PowerShell script deleted)

**CI testing**:
- Add a test job that deliberately allocates GPU memory
- Verify the CSV shows increasing usage
- Confirm artifact is uploaded

### Failure Mode Testing

**Scenarios to test**:
1. ✅ Test times out → Monitor should still upload logs
2. ✅ Test crashes → Monitor should still upload logs
3. ✅ Monitor process is killed → Cleanup step should handle gracefully
4. ✅ GPU tool not available → Monitor should fail gracefully with clear error
5. ✅ Disk full → Monitor should log error but not fail the job

### Validation Criteria

**Before rollout**:
- [ ] Monitoring works on at least 2 Linux runners with different GPUs
- [ ] Monitoring works on at least 2 Windows runners with different GPUs
- [ ] Artifacts are uploaded 100% of the time (including failures)
- [ ] CSV files are valid and parseable by pandas
- [ ] Summary contains accurate peak usage values
- [ ] No job failures caused by monitoring script

---

## Rollout Plan

### Phase 1: Development & Internal Testing (Week 1)

**Scope**: Implementation on a feature branch

**Tasks**:
- Complete Python script implementation
- Complete workflow integration
- Test manually on dev runners
- Code review with CI team

**Success criteria**: 
- ✅ Works on sample Linux and Windows jobs
- ✅ Code review approved

### Phase 2: Canary Deployment (Week 2)

**Scope**: 10% of test jobs (select low-risk components)

**Selected components**:
- Unit tests (fast, stable)
- Small integration tests

**Monitoring**:
- Track job success rate
- Monitor for new failure modes
- Check artifact upload success rate
- Measure performance impact

**Rollback plan**: 
- If job failure rate increases >5%, disable monitoring
- If artifact upload fails >10%, investigate and fix

**Success criteria**:
- ✅ No increase in job failures
- ✅ Artifacts uploaded >95% of the time
- ✅ Performance overhead <2%

### Phase 3: Expanded Rollout (Week 2-3)

**Scope**: 50% of test jobs (add medium-risk components)

**Selected components**:
- All unit tests
- Most integration tests
- Selected e2e tests

**Monitoring**: Same as Phase 2

**Success criteria**: Same as Phase 2

### Phase 4: Full Deployment (Week 3)

**Scope**: 100% of test jobs

**Announcement**:
- Email to engineering team
- Link to documentation
- Examples of how to use the logs
- Request feedback

**Success criteria**:
- ✅ All test jobs have monitoring enabled
- ✅ Documentation published
- ✅ At least 5 engineers have used the logs for debugging

### Phase 5: Feedback & Iteration (Week 4+)

**Activities**:
- Collect feedback via survey
- Track usage metrics (artifact download count)
- Identify pain points
- Prioritize improvements

**Potential improvements**:
- Configurable sampling intervals
- Memory threshold alerts
- Better visualization (charts)
- Integration with test reporting tools

---

## Success Metrics

### Primary Metrics

| Metric | Target | Measurement Method |
|--------|--------|--------------------|
| **Artifact upload success rate** | >95% | GitHub Actions API: count uploads vs. jobs |
| **Time to diagnose OOM failures** | <10 minutes | Survey: ask engineers how long it took |
| **False negative rate** (missed OOMs) | <5% | Manual review: check known OOM failures |
| **Performance overhead** | <2% | Compare job durations before/after |

### Secondary Metrics

| Metric | Target | Measurement Method |
|--------|--------|--------------------|
| Engineer satisfaction | >7/10 | Post-deployment survey |
| Artifact download count | >50/month | GitHub Actions analytics |
| OOM-related rerun rate | -30% | Track "out of memory" in rerun logs |
| Documentation clarity | >8/10 | Survey feedback on docs |

### Leading Indicators (Week 1-2)

- [ ] Monitoring script starts successfully on >99% of jobs
- [ ] No new CI failures introduced by monitoring
- [ ] Artifacts are <100 KB per shard (storage manageable)
- [ ] CSV files are valid and parseable

### Lagging Indicators (Week 4-8)

- [ ] Engineers report faster debugging times
- [ ] Reduction in "unknown failure" classifications
- [ ] Increased confidence in test infrastructure
- [ ] Feature requests for improvements (indicates usage)

---

## Open Questions

### Question 1: Should we monitor CPU tests too?

**Context**: Currently only monitoring GPU test jobs. Some CPU tests also allocate GPU memory indirectly.

**Options**:
1. GPU tests only (current proposal)
2. All tests (CPU + GPU)

**Trade-offs**:
- Option 1: Simpler, less overhead, but might miss some OOMs
- Option 2: Comprehensive, but adds overhead to CPU tests (which don't need it)

**Recommendation**: Start with GPU tests only. Expand to CPU tests if we see demand.

**Decision needed by**: Week 2 (before full rollout)

### Question 2: What sampling interval is optimal?

**Context**: Current proposal is 5 seconds. Lower = more granular, higher = less overhead.

**Options**:
1. 1 second: Very granular, ~1% overhead
2. 5 seconds: Balanced (current)
3. 10 seconds: Minimal overhead, might miss spikes

**Recommendation**: Start with 5 seconds. Make it configurable per-component if needed.

**Decision needed by**: Week 1 (before canary)

### Question 3: Should we implement memory thresholds?

**Context**: Should we fail tests early if memory exceeds threshold (e.g., 95%)?

**Pros**:
- Prevents crashes
- Clearer failure mode

**Cons**:
- False positives: Some tests legitimately use >95%
- More complex logic
- Requires per-component configuration

**Recommendation**: Not in v1. Add in v2 based on data from v1.

**Decision needed by**: Week 4 (after initial feedback)

### Question 4: How to handle multi-GPU tests?

**Context**: Some tests use multiple GPUs. Should we aggregate or report separately?

**Current approach**: Report each GPU separately in CSV.

**Alternatives**:
- Aggregate: Show total usage across all GPUs
- Separate: Show per-GPU usage (current)

**Recommendation**: Keep current approach (per-GPU). Users can aggregate in analysis if needed.

**Decision needed by**: Week 1

### Question 5: Should we archive logs to long-term storage?

**Context**: GitHub artifacts expire after 7 days. Should we archive to S3 for historical analysis?

**Pros**:
- Historical trends
- Long-term capacity planning

**Cons**:
- Additional cost (~$100/month for storage)
- Requires data pipeline setup
- Unclear if anyone would use it

**Recommendation**: Not in v1. Revisit in 3 months if there's demand.

**Decision needed by**: Week 8 (post-deployment review)

---

## References

### Internal Documentation

- [TheRock CI Architecture](../docs/ci_architecture.md) (internal link)
- [Test Sharding Strategy](../docs/test_sharding.md) (internal link)
- [GitHub Actions Best Practices](../docs/github_actions.md) (internal link)

### External Resources

- [ROCm SMI Documentation](https://github.com/RadeonOpenCompute/rocm_smi_lib)
- [AMD SMI CLI Reference](https://rocm.docs.amd.com/projects/amdsmi/en/latest/)
- [Windows Performance Counters for GPUs](https://learn.microsoft.com/en-us/windows/win32/perfctrs/about-performance-counters)
- [GitHub Actions Artifacts API](https://docs.github.com/en/actions/using-workflows/storing-workflow-data-as-artifacts)

### Related Work

- NVIDIA DCGM: Data Center GPU Manager (enterprise solution)
- Grafana GPU Monitoring: Requires Prometheus setup
- AMD Infinity Hub: AMD-specific cloud monitoring

### Prior Art

- Jenkins GPU Plugin: Monitors GPU but not integrated with tests
- GitLab CI GPU Monitoring: Similar concept, Linux-only
- AWS CodeBuild GPU Metrics: CloudWatch integration (not applicable to GitHub Actions)

---

## Appendix A: Example Debugging Workflow

### Scenario: Test Fails with "HIP Runtime Error"

**Step 1**: Check test logs for error message
```
RuntimeError: HIP error: out of resources
at test_large_model.py:45
```

**Step 2**: Download GPU memory artifact
- Go to failed workflow run
- Scroll to "Artifacts" section
- Download `gpu-memory-logs-mlir_tests-shard-2`

**Step 3**: Open summary log
```
=== Peak GPU Memory Usage ===
  GPU0: 15892.34 MB
  GPU1: 14234.56 MB
```

**Step 4**: Check GPU capacity
```bash
# From sanity check logs
GPU0: 16384 MB total
```

**Step 5**: Calculate utilization
```
15892 / 16384 = 97.0% utilization
```

**Conclusion**: ✅ **Confirmed OOM failure** - GPU0 reached 97% usage before crash.

**Next steps**:
- Option 1: Optimize test to use less memory
- Option 2: Request runner with larger GPU (24GB)
- Option 3: Split test into smaller shards

**Time to diagnosis**: ~5 minutes (vs. ~2 hours without monitoring)

---

## Appendix B: CSV Analysis Examples

### Example 1: Find Peak Usage with pandas

```python
import pandas as pd

df = pd.read_csv('gpu_memory_detail_20260116_103015.csv')
df['timestamp'] = pd.to_datetime(df['timestamp'])

# Find peak usage per GPU
peak = df.groupby('gpu_id')['used_mb'].max()
print(peak)

# Plot memory over time
import matplotlib.pyplot as plt
for gpu in df['gpu_id'].unique():
    gpu_df = df[df['gpu_id'] == gpu]
    plt.plot(gpu_df['timestamp'], gpu_df['used_mb'], label=gpu)
plt.legend()
plt.ylabel('Memory Usage (MB)')
plt.xlabel('Time')
plt.title('GPU Memory During Test Execution')
plt.show()
```

### Example 2: Detect Memory Leaks

```python
# Calculate memory growth rate
df = df.sort_values('timestamp')
df['memory_delta'] = df.groupby('gpu_id')['used_mb'].diff()

# Flag if memory consistently increases
leak_threshold = 100  # MB per sample
potential_leak = df[df['memory_delta'] > leak_threshold]

if len(potential_leak) > 5:
    print("WARNING: Potential memory leak detected")
    print(potential_leak[['timestamp', 'gpu_id', 'used_mb', 'memory_delta']])
```

### Example 3: Identify Memory Spikes

```python
# Find sudden spikes (>50% increase in one sample)
df['spike'] = df['memory_delta'] / df['used_mb'].shift(1) > 0.5

spikes = df[df['spike'] == True]
print(f"Found {len(spikes)} memory spikes:")
print(spikes[['timestamp', 'gpu_id', 'used_mb', 'memory_delta']])
```

---

## Appendix C: Comparison with Other Solutions

| Feature | This RFC | DCGM | Prometheus+Exporter | Test Instrumentation |
|---------|----------|------|---------------------|---------------------|
| **Cross-platform** | ✅ | ❌ (NVIDIA only) | ✅ | ✅ |
| **No code changes** | ✅ | ✅ | ✅ | ❌ |
| **Low overhead** | ✅ | ✅ | ⚠️ | ❌ |
| **Easy setup** | ✅ | ❌ | ❌ | ⚠️ |
| **Granular (per-test)** | ⚠️ | ⚠️ | ⚠️ | ✅ |
| **Historical data** | ⚠️ (7 days) | ✅ | ✅ | ❌ |
| **Cost** | Free | Enterprise | Medium | Free |
| **Maintenance** | Low | Medium | High | High |

**Conclusion**: This RFC provides the best balance of features, ease of use, and cost for our CI environment.

---

## Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-01-16 | CI/CD Team | Initial proposal |

---

## Approval

**Stakeholders**:
- [ ] CI/CD Team Lead
- [ ] Test Infrastructure Engineer
- [ ] Security Team (privacy review)
- [ ] Engineering Manager

**Approval Status**: Pending Review

**Next Review Date**: 2026-01-23

---

*This RFC follows the [TheRock RFC Template](../docs/rfc_template.md)*
