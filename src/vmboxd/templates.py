"""VM templates offered when creating a new virtual machine.

``usb`` selects the USB controller the guest has drivers for (Windows 7
has no xHCI driver, so it gets EHCI/OHCI - otherwise the USB tablet mouse
would not work during setup).  ``nic`` is the emulated network card.
"""

TEMPLATES = [
    {"id": "windows11", "label": "Windows 11", "ostype": "Windows11_64", "memory": 8192, "cpus": 4,
     "disk_gb": 80, "firmware": "efi", "tpm": True, "secure_boot": True, "graphics": "vboxsvga", "vram": 256,
     "accelerate3d": True, "usb": "xhci", "nic": "82540EM", "family": "windows"},
    {"id": "windows10", "label": "Windows 10", "ostype": "Windows10_64", "memory": 4096, "cpus": 2,
     "disk_gb": 64, "firmware": "efi", "tpm": False, "secure_boot": False, "graphics": "vboxsvga", "vram": 256,
     "accelerate3d": True, "usb": "xhci", "nic": "82540EM", "family": "windows"},
    {"id": "windows7", "label": "Windows 7", "ostype": "Windows7_64", "memory": 4096, "cpus": 2,
     "disk_gb": 50, "firmware": "bios", "tpm": False, "secure_boot": False, "graphics": "vboxsvga", "vram": 256,
     "accelerate3d": True, "usb": "ehci", "nic": "82540EM", "family": "windows"},
    {"id": "ubuntu", "label": "Ubuntu", "ostype": "Ubuntu_64", "memory": 4096, "cpus": 2,
     "disk_gb": 40, "firmware": "efi", "tpm": False, "secure_boot": False, "graphics": "vmsvga", "vram": 128,
     "accelerate3d": True, "usb": "xhci", "nic": "virtio", "family": "linux"},
    {"id": "debian", "label": "Debian", "ostype": "Debian_64", "memory": 2048, "cpus": 2,
     "disk_gb": 32, "firmware": "efi", "tpm": False, "secure_boot": False, "graphics": "vmsvga", "vram": 128,
     "accelerate3d": True, "usb": "xhci", "nic": "virtio", "family": "linux"},
    {"id": "fedora", "label": "Fedora", "ostype": "Fedora_64", "memory": 4096, "cpus": 2,
     "disk_gb": 40, "firmware": "efi", "tpm": False, "secure_boot": False, "graphics": "vmsvga", "vram": 128,
     "accelerate3d": True, "usb": "xhci", "nic": "virtio", "family": "linux"},
    {"id": "linux", "label": "Linux (generisch)", "ostype": "Linux_64", "memory": 2048, "cpus": 2,
     "disk_gb": 20, "firmware": "bios", "tpm": False, "secure_boot": False, "graphics": "vmsvga", "vram": 128,
     "accelerate3d": True, "usb": "xhci", "nic": "82540EM", "family": "linux"},
    {"id": "service", "label": "Service-VM (headless)", "ostype": "Linux_64", "memory": 512, "cpus": 1,
     "disk_gb": 4, "firmware": "bios", "tpm": False, "secure_boot": False, "graphics": "vmsvga", "vram": 16,
     "accelerate3d": False, "usb": "xhci", "nic": "virtio", "family": "service", "type": "service"},
]

BY_ID = {t["id"]: t for t in TEMPLATES}

COLORS = ["#1e88e5", "#43a047", "#e53935", "#fb8c00", "#8e24aa", "#00acc1", "#fdd835", "#6d4c41"]
