import glob
import os
import shutil
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database.dbCRUD import get_player_by_ID, update_player
from app.dependencies import get_api_key, get_db

# Create photos directory if it doesn't exist
UPLOAD_DIR = Path("uploads/photos")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Allowed image formats
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB

# DiceBear avatar styles for generated avatars
DICEBEAR_STYLES = [
    "adventurer",
    "avataaars",
    "big-ears",
    "bottts",
    "identicon",
    "initials",
    "personas",
]

router = APIRouter(dependencies=[Depends(get_api_key)])


def cleanup_old_player_photos(player_id: str, upload_dir: Path = UPLOAD_DIR):
    """
    Delete all existing uploaded photos for a specific player before uploading a new one.
    This prevents accumulation of old photos and saves storage space.
    """
    try:
        # Find all uploaded photos for this player (pattern: playerid_*.*)
        player_photos_pattern = f"{player_id}_*.*"
        player_photos = glob.glob(str(upload_dir / player_photos_pattern))

        deleted_count = 0
        for photo_path in player_photos:
            try:
                os.remove(photo_path)
                deleted_count += 1
                print(f"🗑️ Deleted old photo: {os.path.basename(photo_path)}")
            except OSError as e:
                print(f"⚠️ Could not delete {photo_path}: {e}")

        if deleted_count > 0:
            print(f"✅ Cleaned up {deleted_count} old photos for player {player_id}")

    except Exception as e:
        print(f"❌ Error during photo cleanup for player {player_id}: {e}")
        # Don't raise exception - continue with upload even if cleanup fails


def cleanup_old_player_photos_safe(
    player_id: str, current_photo_url: str, upload_dir: Path = UPLOAD_DIR
):
    """
    Delete old photos for a player, but keep the current one from database.
    More conservative approach that preserves the currently stored photo.
    """
    try:
        # Extract filename from current photo URL if it exists
        current_filename = None
        if current_photo_url and "/photos/" in current_photo_url:
            current_filename = current_photo_url.split("/")[-1]

        # Find all uploaded photos for this player
        player_photos_pattern = f"{player_id}_*.*"
        player_photos = glob.glob(str(upload_dir / player_photos_pattern))

        deleted_count = 0
        for photo_path in player_photos:
            photo_filename = os.path.basename(photo_path)

            # Skip if this is the current photo in database
            if current_filename and photo_filename == current_filename:
                continue

            try:
                os.remove(photo_path)
                deleted_count += 1
                print(f"🗑️ Deleted old photo: {photo_filename}")
            except OSError as e:
                print(f"⚠️ Could not delete {photo_path}: {e}")

        if deleted_count > 0:
            print(
                f"✅ Safely cleaned up {deleted_count} old photos for player {player_id}"
            )

    except Exception as e:
        print(f"❌ Error during safe photo cleanup for player {player_id}: {e}")


def is_valid_image(filename: str) -> bool:
    """Check if the file has a valid image extension."""
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


@router.post("/upload/{player_id}", tags=["Photos"])
async def upload_player_photo(
    player_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    """
    Upload a profile photo for a player.
    """
    try:
        # Check if player exists
        player = get_player_by_ID(db, player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")

        # Validate file
        if not file.filename:
            raise HTTPException(status_code=400, detail="No file provided")

        if not is_valid_image(file.filename):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
            )

        # Check file size
        file_content = await file.read()
        if len(file_content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="File too large. Max size: 5MB")

        cleanup_old_player_photos(player_id, UPLOAD_DIR)

        # Generate unique filename
        file_extension = Path(file.filename).suffix.lower()
        unique_filename = f"{player_id}_{uuid.uuid4()}{file_extension}"
        file_path = UPLOAD_DIR / unique_filename

        # Save file
        with open(file_path, "wb") as buffer:
            buffer.write(file_content)

        # Update player record with photo URL
        photo_url = f"/photos/{unique_filename}"

        # Update player in database
        from app.database.dbCRUD import update_player_photo

        update_player_photo(db, player_id, photo_url)

        return {
            "message": "Photo uploaded successfully",
            "photo_url": photo_url,
            "filename": unique_filename,
        }
    except HTTPException:
        raise
    except Exception as e:
        # Clean up file if database update fails
        if "file_path" in locals() and file_path.exists():
            file_path.unlink()
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{player_id}/photo", tags=["Photos"])
async def delete_player_photo(player_id: str, db: Session = Depends(get_db)):
    """
    Delete a player's profile photo.
    """
    try:
        player = get_player_by_ID(db, player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")

        if not player.profile_photo_url:
            raise HTTPException(status_code=404, detail="Player has no photo to delete")

        # Extract filename from URL
        filename = player.profile_photo_url.split("/")[-1]
        file_path = UPLOAD_DIR / filename

        # Delete file if it exists
        if file_path.exists():
            file_path.unlink()

        # Update database
        from app.database.dbCRUD import update_player_photo

        update_player_photo(db, player_id, None)

        return {"message": "Photo deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/avatar/{player_id}", tags=["Photos"])
async def set_player_avatar(
    player_id: str,
    avatar_style: str,
    avatar_seed: str = "default",
    db: Session = Depends(get_db),
):
    """
    Set a DiceBear generated avatar for a player.
    """
    try:
        # Check if player exists
        player = get_player_by_ID(db, player_id)
        if not player:
            raise HTTPException(status_code=404, detail="Player not found")

        # Validate avatar style
        if avatar_style not in DICEBEAR_STYLES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid avatar style. Available: {', '.join(DICEBEAR_STYLES)}",
            )

        # Clean up old uploaded photos since we're switching to avatar
        # Avatars are external URLs, so we can safely delete all uploaded files
        cleanup_old_player_photos(player_id, UPLOAD_DIR)

        # Generate DiceBear avatar URL
        avatar_url = (
            f"https://api.dicebear.com/7.x/{avatar_style}/png?seed={avatar_seed}"
        )

        # Update player in database
        from app.database.dbCRUD import update_player_photo

        update_player_photo(db, player_id, avatar_url)

        return {
            "message": "Avatar set successfully",
            "photo_url": avatar_url,
            "avatar_style": avatar_style,
            "avatar_seed": avatar_seed,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/avatars", tags=["Photos"])
async def get_available_avatars():
    """
    Get list of available DiceBear generated avatars.
    """
    try:
        avatars = []
        for style in DICEBEAR_STYLES:
            # Generate 20 different avatars per style using numeric seeds
            for seed in range(1, 21):
                avatars.append(
                    {
                        "name": f"{style}-{seed}",
                        "style": style,
                        "seed": str(seed),
                        "url": f"https://api.dicebear.com/7.x/{style}/png?seed={seed}",
                        "preview_url": f"https://api.dicebear.com/7.x/{style}/png?seed={seed}&size=64",  # Small preview
                    }
                )

        return {
            "avatars": avatars,
            "total_count": len(avatars),
            "styles": DICEBEAR_STYLES,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/avatars/styles", tags=["Photos"])
async def get_avatar_styles():
    """
    Get available avatar styles only.
    """
    try:
        return {
            "styles": [
                {
                    "name": style,
                    "example_url": f"https://api.dicebear.com/7.x/{style}/png?seed=example",
                    "description": f"DiceBear {style} style avatars",
                }
                for style in DICEBEAR_STYLES
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/avatars/generate/{style}", tags=["Photos"])
async def generate_avatar_preview(style: str, seed: str = "preview", size: int = 128):
    """
    Generate a preview of an avatar with specific style and seed.
    """
    try:
        if style not in DICEBEAR_STYLES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid avatar style. Available: {', '.join(DICEBEAR_STYLES)}",
            )

        # Validate size (DiceBear supports sizes up to 512)
        if size < 16 or size > 512:
            size = 128

        avatar_url = f"https://api.dicebear.com/7.x/{style}/png?seed={seed}&size={size}"

        return {"style": style, "seed": seed, "size": size, "url": avatar_url}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{filename}", tags=["Photos"])
async def get_photo(filename: str):
    """
    Serve a photo file.
    """
    try:
        file_path = UPLOAD_DIR / filename

        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Photo not found")

        # You might want to use FileResponse here for better performance
        from fastapi.responses import FileResponse

        return FileResponse(
            path=file_path,
            media_type="image/*",
            headers={"Cache-Control": "max-age=3600"},  # Cache for 1 hour
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/maintenance/cleanup-orphaned", tags=["Photos"])
async def cleanup_orphaned_photos(db: Session = Depends(get_db)):
    """
    Maintenance endpoint to clean up orphaned photos (files that exist but aren't referenced in database).
    Use with caution - only run this occasionally for maintenance.
    """
    try:
        from app.database.dbCRUD import get_all_players

        # Get all uploaded photo files
        all_photo_files = glob.glob(str(UPLOAD_DIR / "*_*.*"))

        # Get all players with photos from database
        players = get_all_players(db)  # You'll need to implement this in dbCRUD
        referenced_files = set()

        for player in players:
            if player.profile_photo_url and "/photos/" in player.profile_photo_url:
                filename = player.profile_photo_url.split("/")[-1]
                referenced_files.add(str(UPLOAD_DIR / filename))

        # Find orphaned files
        orphaned_files = [f for f in all_photo_files if f not in referenced_files]

        deleted_count = 0
        for orphaned_file in orphaned_files:
            try:
                os.remove(orphaned_file)
                deleted_count += 1
                print(f"🗑️ Deleted orphaned photo: {os.path.basename(orphaned_file)}")
            except OSError as e:
                print(f"⚠️ Could not delete {orphaned_file}: {e}")

        return {
            "message": "Orphaned photo cleanup completed",
            "total_files_found": len(all_photo_files),
            "referenced_files": len(referenced_files),
            "orphaned_files_deleted": deleted_count,
            "storage_saved": f"~{deleted_count * 0.5}MB (estimated)",
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/maintenance/storage-info", tags=["Photos"])
async def get_storage_info():
    """
    Get information about photo storage usage.
    """
    try:
        all_files = glob.glob(str(UPLOAD_DIR / "*.*"))
        total_size = sum(os.path.getsize(f) for f in all_files)

        return {
            "upload_directory": str(UPLOAD_DIR),
            "total_files": len(all_files),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "average_file_size_kb": (
                round((total_size / len(all_files)) / 1024, 2) if all_files else 0
            ),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
