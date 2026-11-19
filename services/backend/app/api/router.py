from fastapi import APIRouter

from .v1.auth import router as auth_router
from .v1.trips import router as trips_router

router = APIRouter(prefix="/v1")
router.include_router(auth_router)
router.include_router(trips_router)
