"""System statistics collection for Jetson and Linux systems."""
import os
import logging
import subprocess
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class SystemStats:
    """Collect system statistics including Jetson-specific metrics."""

    def __init__(self):
        self._is_jetson = self._detect_jetson()
        if self._is_jetson:
            logger.info("Jetson device detected")

    def _detect_jetson(self) -> bool:
        """Check if running on a Jetson device."""
        # Check for Jetson-specific files
        jetson_indicators = [
            "/etc/nv_tegra_release",
            "/sys/devices/gpu.0",
            "/sys/class/thermal/thermal_zone0/type"
        ]
        for path in jetson_indicators:
            if os.path.exists(path):
                return True

        # Check for tegrastats
        try:
            result = subprocess.run(["which", "tegrastats"], capture_output=True)
            if result.returncode == 0:
                return True
        except Exception:
            pass

        return False

    def get_all_stats(self) -> Dict:
        """Get all system statistics."""
        return {
            "cpu": self.get_cpu_stats(),
            "memory": self.get_memory_stats(),
            "gpu": self.get_gpu_stats(),
            "disk": self.get_disk_stats(),
            "temperature": self.get_temperature_stats(),
            "network": self.get_network_stats(),
            "system": self.get_system_info(),
            "is_jetson": self._is_jetson,
        }

    def get_cpu_stats(self) -> Dict:
        """Get CPU usage statistics."""
        result = {
            "usage_percent": 0,
            "cores": os.cpu_count() or 0,
            "load_avg": [0, 0, 0],
        }

        # Get load average
        try:
            with open("/proc/loadavg", "r") as f:
                parts = f.read().split()[:3]
                result["load_avg"] = [float(parts[0]), float(parts[1]), float(parts[2])]
        except Exception as e:
            logger.debug(f"Could not read load average: {e}")

        # Get CPU usage from /proc/stat
        try:
            with open("/proc/stat", "r") as f:
                line = f.readline()
                parts = line.split()
                if parts[0] == "cpu":
                    user, nice, system, idle, iowait = map(int, parts[1:6])
                    total = user + nice + system + idle + iowait
                    usage = ((total - idle) / total) * 100 if total > 0 else 0
                    result["usage_percent"] = round(usage, 1)
        except Exception as e:
            logger.debug(f"Could not read CPU stats: {e}")
            # Fallback: estimate from load average
            if result["load_avg"][0] > 0:
                result["usage_percent"] = round(min((result["load_avg"][0] / result["cores"]) * 100, 100), 1)

        return result

    def get_memory_stats(self) -> Dict:
        """Get RAM usage statistics."""
        try:
            with open("/proc/meminfo", "r") as f:
                meminfo = {}
                for line in f:
                    parts = line.split()
                    key = parts[0].rstrip(":")
                    value = int(parts[1]) * 1024  # Convert KB to bytes
                    meminfo[key] = value

                total = meminfo.get("MemTotal", 0)
                free = meminfo.get("MemFree", 0)
                buffers = meminfo.get("Buffers", 0)
                cached = meminfo.get("Cached", 0)
                available = meminfo.get("MemAvailable", free + buffers + cached)

                used = total - available
                usage = (used / total) * 100 if total > 0 else 0

                return {
                    "total_bytes": total,
                    "used_bytes": used,
                    "available_bytes": available,
                    "usage_percent": round(usage, 1),
                    "total_gb": round(total / (1024**3), 1),
                    "used_gb": round(used / (1024**3), 1),
                }
        except Exception as e:
            logger.debug(f"Could not read memory stats: {e}")

        return {"usage_percent": 0, "total_gb": 0, "used_gb": 0}

    def get_gpu_stats(self) -> Dict:
        """Get GPU usage statistics (Jetson or NVIDIA)."""
        stats = {
            "available": False,
            "usage_percent": 0,
            "memory_used_mb": 0,
            "memory_total_mb": 0,
        }

        # Try Jetson GPU stats first
        if self._is_jetson:
            jetson_stats = self._get_jetson_gpu_stats()
            if jetson_stats:
                return jetson_stats

        # Try nvidia-smi for discrete GPUs
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split(",")
                if len(parts) >= 3:
                    return {
                        "available": True,
                        "usage_percent": float(parts[0].strip()),
                        "memory_used_mb": float(parts[1].strip()),
                        "memory_total_mb": float(parts[2].strip()),
                        "name": self._get_gpu_name(),
                    }
        except Exception as e:
            logger.debug(f"nvidia-smi not available: {e}")

        return stats

    def _get_jetson_gpu_stats(self) -> Optional[Dict]:
        """Get Jetson GPU statistics."""
        stats = {
            "available": True,
            "usage_percent": 0,
            "memory_used_mb": 0,
            "memory_total_mb": 0,
            "name": "Jetson GPU",
        }

        # Try reading GPU load from sysfs
        gpu_load_paths = [
            "/sys/devices/gpu.0/load",
            "/sys/devices/platform/gpu.0/load",
            "/sys/devices/57000000.gpu/load",
            "/sys/devices/17000000.ga10b/load",  # Orin
        ]

        for path in gpu_load_paths:
            if os.path.exists(path):
                try:
                    with open(path, "r") as f:
                        # Load is usually 0-1000 (permille)
                        load = int(f.read().strip())
                        stats["usage_percent"] = load / 10.0
                        break
                except Exception:
                    continue

        # GPU memory on Jetson is shared with system RAM
        # Get total system memory as reference
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        total_kb = int(line.split()[1])
                        stats["memory_total_mb"] = total_kb / 1024
                        stats["note"] = "Shared memory with CPU"
                        break
        except Exception:
            pass

        # Try to get Jetson model name
        try:
            if os.path.exists("/proc/device-tree/model"):
                with open("/proc/device-tree/model", "r") as f:
                    model = f.read().strip().replace("\x00", "")
                    stats["name"] = model
        except Exception:
            pass

        return stats if stats["usage_percent"] > 0 or stats["memory_total_mb"] > 0 else None

    def _get_gpu_name(self) -> str:
        """Get GPU name."""
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return "NVIDIA GPU"

    def get_disk_stats(self) -> Dict:
        """Get disk usage statistics."""
        try:
            stat = os.statvfs("/")
            total = stat.f_blocks * stat.f_frsize
            free = stat.f_bfree * stat.f_frsize
            used = total - free
            usage = (used / total) * 100 if total > 0 else 0

            return {
                "total_bytes": total,
                "used_bytes": used,
                "free_bytes": free,
                "usage_percent": round(usage, 1),
                "total_gb": round(total / (1024**3), 1),
                "used_gb": round(used / (1024**3), 1),
                "free_gb": round(free / (1024**3), 1),
            }
        except Exception as e:
            logger.debug(f"Could not read disk stats: {e}")

        return {"usage_percent": 0, "total_gb": 0, "used_gb": 0}

    def get_temperature_stats(self) -> Dict:
        """Get temperature readings."""
        temps = {}

        # Read from thermal zones
        thermal_base = Path("/sys/class/thermal")
        if thermal_base.exists():
            for zone in thermal_base.glob("thermal_zone*"):
                try:
                    type_file = zone / "type"
                    temp_file = zone / "temp"
                    if type_file.exists() and temp_file.exists():
                        zone_type = type_file.read_text().strip()
                        temp_mc = int(temp_file.read_text().strip())
                        temp_c = temp_mc / 1000.0
                        temps[zone_type] = round(temp_c, 1)
                except Exception:
                    continue

        # Jetson-specific temperature sensors
        jetson_temp_paths = {
            "CPU": "/sys/devices/virtual/thermal/thermal_zone0/temp",
            "GPU": "/sys/devices/virtual/thermal/thermal_zone1/temp",
            "AUX": "/sys/devices/virtual/thermal/thermal_zone2/temp",
        }

        for name, path in jetson_temp_paths.items():
            if os.path.exists(path) and name not in temps:
                try:
                    with open(path, "r") as f:
                        temp_mc = int(f.read().strip())
                        temps[name] = round(temp_mc / 1000.0, 1)
                except Exception:
                    continue

        # Calculate max/average
        if temps:
            values = list(temps.values())
            temps["_max"] = max(values)
            temps["_avg"] = round(sum(values) / len(values), 1)

        return temps

    def get_network_stats(self) -> Dict:
        """Get network interface statistics."""
        stats = {}

        try:
            with open("/proc/net/dev", "r") as f:
                lines = f.readlines()[2:]  # Skip headers

                for line in lines:
                    parts = line.split()
                    if len(parts) < 10:
                        continue

                    iface = parts[0].rstrip(":")
                    if iface in ("lo",):
                        continue

                    rx_bytes = int(parts[1])
                    tx_bytes = int(parts[9])

                    stats[iface] = {
                        "rx_bytes": rx_bytes,
                        "tx_bytes": tx_bytes,
                        "rx_mb": round(rx_bytes / (1024**2), 1),
                        "tx_mb": round(tx_bytes / (1024**2), 1),
                    }
        except Exception as e:
            logger.debug(f"Could not read network stats: {e}")

        return stats

    def get_system_info(self) -> Dict:
        """Get general system information."""
        info = {
            "hostname": "unknown",
            "uptime_seconds": 0,
            "uptime_formatted": "",
        }

        # Hostname
        try:
            with open("/etc/hostname", "r") as f:
                info["hostname"] = f.read().strip()
        except Exception:
            try:
                info["hostname"] = os.uname().nodename
            except Exception:
                pass

        # Uptime
        try:
            with open("/proc/uptime", "r") as f:
                uptime = float(f.read().split()[0])
                info["uptime_seconds"] = int(uptime)
                info["uptime_formatted"] = self._format_uptime(uptime)
        except Exception:
            pass

        # Kernel version
        try:
            info["kernel"] = os.uname().release
        except Exception:
            pass

        # Jetson info
        if self._is_jetson:
            try:
                if os.path.exists("/etc/nv_tegra_release"):
                    with open("/etc/nv_tegra_release", "r") as f:
                        info["jetpack"] = f.read().strip()[:50]
            except Exception:
                pass

            try:
                if os.path.exists("/proc/device-tree/model"):
                    with open("/proc/device-tree/model", "r") as f:
                        info["model"] = f.read().strip().replace("\x00", "")
            except Exception:
                pass

        return info

    def _format_uptime(self, seconds: float) -> str:
        """Format uptime in human-readable format."""
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        mins = int((seconds % 3600) // 60)

        if days > 0:
            return f"{days}d {hours}h {mins}m"
        elif hours > 0:
            return f"{hours}h {mins}m"
        else:
            return f"{mins}m"


# Singleton instance
_system_stats = None


def get_system_stats() -> SystemStats:
    """Get the system stats singleton."""
    global _system_stats
    if _system_stats is None:
        _system_stats = SystemStats()
    return _system_stats
