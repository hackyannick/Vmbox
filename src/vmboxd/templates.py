"""VM templates offered when creating a new virtual machine."""

TEMPLATES = [
    {"id": "windows11", "label": "Windows 11", "ostype": "Windows11_64", "memory": 8192, "cpus": 4,
     "disk_gb": 80, "firmware": "efi", "tpm": True, "secure_boot": True, "graphics": "vboxsvga", "vram": 128,
     "family": "windows"},
    {"id": "windows10", "label": "Windows 10", "ostype": "Windows10_64", "memory": 4096, "cpus": 2,
     "disk_gb": 64, "firmware": "efi", "tpm": False, "secure_boot": False, "graphics": "vboxsvga", "vram": 128,
     "family": "windows"},
    {"id": "ubuntu", "label": "Ubuntu", "ostype": "Ubuntu_64", "memory": 4096, "cpus": 2,
     "disk_gb": 40, "firmware": "efi", "tpm": False, "secure_boot": False, "graphics": "vmsvga", "vram": 128,
     "family": "linux"},
    {"id": "debian", "label": "Debian", "ostype": "Debian_64", "memory": 2048, "cpus": 2,
     "disk_gb": 32, "firmware": "efi", "tpm": False, "secure_boot": False, "graphics": "vmsvga", "vram": 64,
     "family": "linux"},
    {"id": "fedora", "label": "Fedora", "ostype": "Fedora_64", "memory": 4096, "cpus": 2,
     "disk_gb": 40, "firmware": "efi", "tpm": False, "secure_boot": False, "graphics": "vmsvga", "vram": 128,
     "family": "linux"},
    {"id": "linux", "label": "Linux (generisch)", "ostype": "Linux_64", "memory": 2048, "cpus": 2,
     "disk_gb": 20, "firmware": "bios", "tpm": False, "secure_boot": False, "graphics": "vmsvga", "vram": 32,
     "family": "linux"},
    {"id": "service", "label": "Service-VM (headless)", "ostype": "Linux_64", "memory": 512, "cpus": 1,
     "disk_gb": 4, "firmware": "bios", "tpm": False, "secure_boot": False, "graphics": "vmsvga", "vram": 16,
     "family": "service", "type": "service"},
]

BY_ID = {t["id"]: t for t in TEMPLATES}

COLORS = ["#1e88e5", "#43a047", "#e53935", "#fb8c00", "#8e24aa", "#00acc1", "#fdd835", "#6d4c41"]
