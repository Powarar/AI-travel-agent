from fastapi import APIRouter

from .v1.trips import router as trips_router

router = APIRouter(prefix="/v1")
router.include_router(trips_router)
