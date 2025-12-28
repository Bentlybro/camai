"""System stats API routes."""
import logging
from fastapi import APIRouter

from system_stats import get_system_stats

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("")
async def get_all_stats():
    """Get all system statistics."""
    stats = get_system_stats()
    return stats.get_all_stats()


@router.get("/cpu")
async def get_cpu_stats():
    """Get CPU statistics."""
    stats = get_system_stats()
    return stats.get_cpu_stats()


@router.get("/memory")
async def get_memory_stats():
    """Get memory/RAM statistics."""
    stats = get_system_stats()
    return stats.get_memory_stats()


@router.get("/gpu")
async def get_gpu_stats():
    """Get GPU statistics."""
    stats = get_system_stats()
    return stats.get_gpu_stats()


@router.get("/disk")
async def get_disk_stats():
    """Get disk usage statistics."""
    stats = get_system_stats()
    return stats.get_disk_stats()


@router.get("/temperature")
async def get_temperature_stats():
    """Get temperature readings."""
    stats = get_system_stats()
    return stats.get_temperature_stats()


@router.get("/network")
async def get_network_stats():
    """Get network interface statistics."""
    stats = get_system_stats()
    return stats.get_network_stats()


@router.get("/info")
async def get_system_info():
    """Get general system information."""
    stats = get_system_stats()
    return stats.get_system_info()
