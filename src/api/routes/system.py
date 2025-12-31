"""System stats API routes."""
import logging
from fastapi import APIRouter, Depends

from system_stats import get_system_stats
from auth.dependencies import get_current_user, CurrentUser

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("")
async def get_all_stats(user: CurrentUser = Depends(get_current_user)):
    """Get all system statistics (authenticated)."""
    stats = get_system_stats()
    return stats.get_all_stats()


@router.get("/cpu")
async def get_cpu_stats(user: CurrentUser = Depends(get_current_user)):
    """Get CPU statistics (authenticated)."""
    stats = get_system_stats()
    return stats.get_cpu_stats()


@router.get("/memory")
async def get_memory_stats(user: CurrentUser = Depends(get_current_user)):
    """Get memory/RAM statistics (authenticated)."""
    stats = get_system_stats()
    return stats.get_memory_stats()


@router.get("/gpu")
async def get_gpu_stats(user: CurrentUser = Depends(get_current_user)):
    """Get GPU statistics (authenticated)."""
    stats = get_system_stats()
    return stats.get_gpu_stats()


@router.get("/disk")
async def get_disk_stats(user: CurrentUser = Depends(get_current_user)):
    """Get disk usage statistics (authenticated)."""
    stats = get_system_stats()
    return stats.get_disk_stats()


@router.get("/temperature")
async def get_temperature_stats(user: CurrentUser = Depends(get_current_user)):
    """Get temperature readings (authenticated)."""
    stats = get_system_stats()
    return stats.get_temperature_stats()


@router.get("/network")
async def get_network_stats(user: CurrentUser = Depends(get_current_user)):
    """Get network interface statistics (authenticated)."""
    stats = get_system_stats()
    return stats.get_network_stats()


@router.get("/info")
async def get_system_info(user: CurrentUser = Depends(get_current_user)):
    """Get general system information (authenticated)."""
    stats = get_system_stats()
    return stats.get_system_info()
