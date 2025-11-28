# GEGARANDEERD CORRECTE VERSIE VAN get_original_video FUNCTIE
# Copy-paste deze functie om de oude te vervangen in app/main.py

@app.get("/videos/{video_id}/original")
async def get_original_video(request: Request, video_id: str):
    # First try to find as a video directory
    video_dir = _find_video_directory(video_id)
    if video_dir and video_dir.exists():
        # Check privacy (video itself or parent folder)
        info = _load_video_info(video_dir)
        video_is_private = info.get("is_private", False) or _is_folder_private(info.get("folder_path"))
        if video_is_private and not is_editor(request):
            return JSONResponse({"error": "Accès refusé"}, status_code=403)

        original_path = _find_original_video(video_dir)
        if original_path is not None:
            meta = _load_video_metadata(video_dir)
            filename = meta.filename if meta else original_path.name
            return FileResponse(original_path, filename=filename)
    
    # If not found as video directory, try to find as loose video file
    loose_file = _find_loose_file(video_id)
    if loose_file and loose_file.exists() and loose_file.is_file():
        # Check if it's a video file
        if loose_file.suffix.lower() in [".mp4", ".avi", ".mov", ".mkv", ".webm"]:
            # Check privacy based on folder
            try:
                rel_path = loose_file.parent.relative_to(settings.PROCESSED_DIR)
                folder_path = str(rel_path) if str(rel_path) != "." else None
            except ValueError:
                folder_path = None
            
            if not is_editor(request) and _is_folder_private(folder_path):
                return JSONResponse({"error": "Accès refusé"}, status_code=403)
            
            return FileResponse(loose_file, filename=loose_file.name)
    
    return JSONResponse({"error": "Vidéo non trouvée"}, status_code=404)



