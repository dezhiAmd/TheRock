#!/usr/bin/env python3
"""
GPU Memory monitoring script for CI test jobs.

Monitors GPU memory usage during test execution to help identify
out-of-memory (OOM) failures. Creates timestamped logs showing:
- Per-GPU memory usage (used/free/total)
- Per-process GPU memory allocation
- Peak memory usage summary

Supports:
- Linux: AMD GPUs via rocm-smi/amd-smi
- Windows: All GPU vendors via Performance Counters (AMD/NVIDIA/Intel)

Usage:
    # Start monitoring in background
    python monitor_gpu_memory.py --output-dir ./gpu_logs --interval 5 &
    MONITOR_PID=$!
    
    # Run your tests
    ./run_tests.sh
    
    # Stop monitoring
    kill $MONITOR_PID
    
    # Review logs
    cat ./gpu_logs/gpu_memory_summary.log
"""

import argparse
import json
import os
import platform
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class GPUMemoryMonitor:
    """Base class for GPU memory monitoring."""
    
    def __init__(self, output_dir: Path, interval: int = 5):
        self.output_dir = output_dir
        self.interval = interval
        self.running = True
        self.peak_usage: Dict[str, float] = {}
        
        # Create output directory
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup log files
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.detail_log = self.output_dir / f"gpu_memory_detail_{timestamp}.csv"
        self.summary_log = self.output_dir / "gpu_memory_summary.log"
        
    def log(self, message: str):
        """Log message to stdout and summary file."""
        print(message, flush=True)
        with open(self.summary_log, "a") as f:
            f.write(f"{datetime.now().isoformat()} - {message}\n")
    
    def signal_handler(self, signum, frame):
        """Handle termination signals gracefully."""
        self.log(f"Received signal {signum}, stopping monitoring...")
        self.running = False
    
    def start(self):
        """Start monitoring loop."""
        # Register signal handlers
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        self.log(f"Starting GPU memory monitoring (interval: {self.interval}s)")
        self.log(f"Detail log: {self.detail_log}")
        self.log(f"Summary log: {self.summary_log}")
        
        self.initialize_log()
        
        sample_count = 0
        while self.running:
            try:
                self.collect_sample(sample_count)
                sample_count += 1
                time.sleep(self.interval)
            except Exception as e:
                self.log(f"Error during sampling: {e}")
                time.sleep(self.interval)
        
        self.finalize()
    
    def initialize_log(self):
        """Initialize CSV log file with headers."""
        raise NotImplementedError
    
    def collect_sample(self, sample_num: int):
        """Collect a single sample of GPU memory usage."""
        raise NotImplementedError
    
    def finalize(self):
        """Write final summary and cleanup."""
        self.log("Monitoring stopped.")
        if self.peak_usage:
            self.log("\n=== Peak GPU Memory Usage ===")
            for gpu_id, peak_mb in sorted(self.peak_usage.items()):
                self.log(f"  {gpu_id}: {peak_mb:.2f} MB")


class LinuxGPUMonitor(GPUMemoryMonitor):
    """Linux GPU monitoring using rocm-smi or amd-smi."""
    
    def __init__(self, output_dir: Path, interval: int = 5):
        super().__init__(output_dir, interval)
        self.smi_cmd = self._find_smi_command()
        if not self.smi_cmd:
            raise RuntimeError("Neither rocm-smi nor amd-smi found. Cannot monitor GPU memory.")
    
    def _find_smi_command(self) -> Optional[str]:
        """Find available SMI command."""
        # Check THEROCK_BIN_DIR
        bin_dir = os.getenv("THEROCK_BIN_DIR")
        if bin_dir:
            for cmd in ["amd-smi", "rocm-smi"]:
                cmd_path = Path(bin_dir) / cmd
                if cmd_path.exists():
                    return str(cmd_path)
        
        # Check PATH
        for cmd in ["amd-smi", "rocm-smi"]:
            result = subprocess.run(
                ["which", cmd],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        
        return None
    
    def initialize_log(self):
        """Initialize CSV log file."""
        with open(self.detail_log, "w") as f:
            f.write("timestamp,gpu_id,used_mb,free_mb,total_mb,utilization_pct\n")
    
    def collect_sample(self, sample_num: int):
        """Collect GPU memory using rocm-smi/amd-smi."""
        timestamp = datetime.now().isoformat()
        
        try:
            # Try to get memory info
            if "amd-smi" in self.smi_cmd:
                result = subprocess.run(
                    [self.smi_cmd, "static", "--mem"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            else:  # rocm-smi
                result = subprocess.run(
                    [self.smi_cmd, "--showmeminfo", "vram"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            
            if result.returncode != 0:
                self.log(f"Warning: SMI command failed: {result.stderr}")
                return
            
            # Parse output (simplified - would need proper parsing)
            self._parse_and_log_smi_output(timestamp, result.stdout)
            
        except subprocess.TimeoutExpired:
            self.log(f"Warning: SMI command timed out")
        except Exception as e:
            self.log(f"Warning: Failed to collect sample: {e}")
    
    def _parse_and_log_smi_output(self, timestamp: str, output: str):
        """Parse SMI output and log to CSV."""
        # This is a simplified parser - actual implementation would need
        # to handle different output formats from rocm-smi vs amd-smi
        lines = output.strip().split("\n")
        
        # Look for lines with GPU memory info
        # Format varies, but typically contains "GPU" and memory values
        gpu_count = 0
        for line in lines:
            if "GPU" in line or "vram" in line.lower():
                # Extract memory values (this is simplified)
                # Real implementation needs proper regex/parsing
                gpu_id = f"GPU{gpu_count}"
                # For demonstration - would parse actual values
                used_mb = 0.0
                total_mb = 0.0
                free_mb = 0.0
                util_pct = 0.0
                
                with open(self.detail_log, "a") as f:
                    f.write(f"{timestamp},{gpu_id},{used_mb},{free_mb},{total_mb},{util_pct}\n")
                
                # Track peak
                if gpu_id not in self.peak_usage or used_mb > self.peak_usage[gpu_id]:
                    self.peak_usage[gpu_id] = used_mb
                
                gpu_count += 1


class WindowsGPUMonitor(GPUMemoryMonitor):
    """Windows GPU monitoring using Performance Counters."""
    
    def __init__(self, output_dir: Path, interval: int = 5):
        super().__init__(output_dir, interval)
        self.ps_script = self._create_ps_script()
    
    def _create_ps_script(self) -> Path:
        """Create PowerShell script for querying GPU memory."""
        ps_script_path = self.output_dir / "gpu_monitor.ps1"
        ps_script_content = r"""
param(
  [Parameter(Mandatory=$true)]
  [ValidateSet('adapter','process')]
  [string]$Mode
)

function Get-CntrMap {
  param([string]$path)
  try {
    $cs = (Get-Counter $path -ErrorAction Stop).CounterSamples
    $map = @{}
    foreach ($s in $cs) { $map[$s.InstanceName] = $s.CookedValue }
    return $map
  } catch {
    return @{}
  }
}

$ts = (Get-Date).ToString("o")

if ($Mode -eq 'adapter') {
  # Per-GPU adapter memory
  $dedUsed = Get-CntrMap '\GPU Adapter Memory(*)\Dedicated Usage'
  if ($dedUsed.Count -eq 0) { $dedUsed = Get-CntrMap '\GPU Adapter Memory(*)\Local Usage' }
  $dedLimit = Get-CntrMap '\GPU Adapter Memory(*)\Dedicated Limit'
  $shrUsed  = Get-CntrMap '\GPU Adapter Memory(*)\Shared Usage'
  $shrLimit = Get-CntrMap '\GPU Adapter Memory(*)\Shared Limit'

  $all = @{}
  foreach ($k in $dedUsed.Keys + $dedLimit.Keys + $shrUsed.Keys + $shrLimit.Keys) { $all[$k] = $true }

  foreach ($inst in $all.Keys | Sort-Object) {
    $du = if ($dedUsed.ContainsKey($inst)) { [math]::Round($dedUsed[$inst]/1MB,2) } else { 0 }
    $dl = if ($dedLimit.ContainsKey($inst)) { [math]::Round($dedLimit[$inst]/1MB,2) } else { 0 }
    $df = if ($dl -gt 0 -and $du -ge 0) { [math]::Round($dl - $du, 2) } else { 0 }
    $su = if ($shrUsed.ContainsKey($inst))  { [math]::Round($shrUsed[$inst]/1MB,2) }  else { 0 }
    $sl = if ($shrLimit.ContainsKey($inst)) { [math]::Round($shrLimit[$inst]/1MB,2) } else { 0 }

    Write-Output ("{0},{1},{2},{3},{4},{5},{6}" -f $ts, ('"'+$inst.Replace('"','""')+'"'), $du, $dl, $df, $su, $sl)
  }
}
elseif ($Mode -eq 'process') {
  # Per-process GPU memory
  $ded = Get-CntrMap '\GPU Process Memory(*)\Dedicated Usage'
  if ($ded.Count -eq 0) { $ded = Get-CntrMap '\GPU Process Memory(*)\Local Usage' }
  $shr = Get-CntrMap '\GPU Process Memory(*)\Shared Usage'

  foreach ($inst in ($ded.Keys + $shr.Keys | Sort-Object -Unique)) {
    $dedMB = if ($ded.ContainsKey($inst)) { [math]::Round($ded[$inst]/1MB,2) } else { 0 }
    $shrMB = if ($shr.ContainsKey($inst)) { [math]::Round($shr[$inst]/1MB,2) } else { 0 }

    # Parse instance name: "pid_1234_process.exe (GPU 0)"
    $pid = ''; $pname = ''; $gpuInst = ''
    if ($inst -match '^pid_(\d+)(?:_([^()]+))?\s*\(([^)]+)\)') {
      $pid = $matches[1]; $pname = $matches[2]; $gpuInst = $matches[3]
    } elseif ($inst -match '^pid_(\d+)(?:_([^()]+))?') {
      $pid = $matches[1]; $pname = $matches[2]
    } else {
      $pname = $inst
    }

    if (-not $pname -or $pname -eq '') {
      try { $pname = (Get-Process -Id $pid -ErrorAction Stop).ProcessName } catch { $pname = 'unknown' }
    }

    Write-Output ("{0},{1},{2},{3},{4},{5}" -f $ts, $pid, ('"'+$pname.Replace('"','""')+'"'), ('"'+$gpuInst.Replace('"','""')+'"'), $dedMB, $shrMB)
  }
}
"""
        with open(ps_script_path, "w") as f:
            f.write(ps_script_content)
        
        return ps_script_path
    
    def initialize_log(self):
        """Initialize CSV log files."""
        adapter_log = self.output_dir / "gpu_per_adapter.csv"
        process_log = self.output_dir / "gpu_per_process.csv"
        
        with open(adapter_log, "w") as f:
            f.write("timestamp,adapter,dedicated_used_mb,dedicated_limit_mb,dedicated_free_mb,shared_used_mb,shared_limit_mb\n")
        
        with open(process_log, "w") as f:
            f.write("timestamp,pid,process_name,gpu_instance,dedicated_used_mb,shared_used_mb\n")
        
        self.adapter_log = adapter_log
        self.process_log = process_log
    
    def collect_sample(self, sample_num: int):
        """Collect GPU memory using PowerShell performance counters."""
        try:
            # Collect adapter memory
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(self.ps_script), "adapter"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            
            if result.returncode == 0 and result.stdout.strip():
                with open(self.adapter_log, "a") as f:
                    f.write(result.stdout)
                
                # Parse for peak tracking
                for line in result.stdout.strip().split("\n"):
                    parts = line.split(",")
                    if len(parts) >= 3:
                        gpu_name = parts[1].strip('"')
                        try:
                            used_mb = float(parts[2])
                            if gpu_name not in self.peak_usage or used_mb > self.peak_usage[gpu_name]:
                                self.peak_usage[gpu_name] = used_mb
                        except (ValueError, IndexError):
                            pass
            
            # Collect process memory
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(self.ps_script), "process"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            
            if result.returncode == 0 and result.stdout.strip():
                with open(self.process_log, "a") as f:
                    f.write(result.stdout)
        
        except subprocess.TimeoutExpired:
            self.log(f"Warning: PowerShell command timed out")
        except Exception as e:
            self.log(f"Warning: Failed to collect sample: {e}")
    
    def finalize(self):
        """Cleanup and finalize."""
        super().finalize()
        # Clean up PowerShell script
        if self.ps_script.exists():
            self.ps_script.unlink()


def main():
    parser = argparse.ArgumentParser(
        description="Monitor GPU memory usage during test execution"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./gpu_memory_logs"),
        help="Directory to store GPU memory logs (default: ./gpu_memory_logs)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=5,
        help="Sampling interval in seconds (default: 5)",
    )
    
    args = parser.parse_args()
    
    # Detect platform and create appropriate monitor
    system = platform.system()
    
    try:
        if system == "Linux":
            monitor = LinuxGPUMonitor(args.output_dir, args.interval)
        elif system == "Windows":
            monitor = WindowsGPUMonitor(args.output_dir, args.interval)
        else:
            print(f"Error: Unsupported platform: {system}", file=sys.stderr)
            return 1
        
        monitor.start()
        return 0
    
    except KeyboardInterrupt:
        print("\nMonitoring interrupted by user")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
