import os
import psutil
import platform

def get_usb_storage_path():
    """
    Scans for a mounted USB drive.
    Returns the path to the first found removable storage or a fallback path.
    """
    system = platform.system()
    
    if system == "Windows":
        # For Windows testing/development
        # Look for a drive that is removable (not C:)
        drives = psutil.disk_partitions()
        for drive in drives:
            if 'removable' in drive.opts or (drive.device != "C:\\" and "cdrom" not in drive.opts):
                 # Simple heuristic for testing: pick first non-C drive or explicit removable
                 # Be careful not to pick system restore partitions etc, usually D: E: etc are fine
                 if os.path.isdir(drive.mountpoint):
                     return os.path.join(drive.mountpoint, "Recordings")
        
        # Fallback for Windows if no USB found
        return os.path.abspath("./Recordings")

    elif system == "Linux":
        # For Raspberry Pi (Linux)
        # Typically USB drives are mounted under /media/pi/ or /mnt/
        # We can check specific valid mount points
        base_media_paths = ["/media/" + os.environ.get("USER", "pi"), "/mnt"]
        
        for base_path in base_media_paths:
            if os.path.exists(base_path):
                # Check for directories inside the base path
                try:
                    mounts = os.listdir(base_path)
                    for mount in mounts:
                        full_path = os.path.join(base_path, mount)
                        if os.path.isdir(full_path):
                            # Verify if it's writable
                            if os.access(full_path, os.W_OK):
                                return os.path.join(full_path, "Recordings")
                except OSError:
                    continue
        
        # Fallback to local user home if no USB
        return os.path.expanduser("~/Recordings")

    return "Recordings"

def ensure_directory(path):
    if not os.path.exists(path):
        os.makedirs(path)
    return path
