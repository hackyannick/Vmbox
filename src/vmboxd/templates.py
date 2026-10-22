"""VM templates offered when creating a new virtual machine.

``usb`` selects the USB controller the guest has drivers for (Windows 7
has no xHCI driver, so it gets EHCI/OHCI - otherwise the USB tablet mouse
would not work during setup).  ``nic`` is the emulated network card.

Windows XP and 2000 need period hardware they ship drivers for: an IDE
controller (no AHCI driver), the PCnet-FAST III network card, AC'97 sound,
a PS/2 mouse and the legacy VBoxVGA adapter (no 3D).  As they no longer get
security updates they start without network by default.

``cpu_profile: legacy`` shows these old systems an older, well-known CPU
instead of the host CPU: Windows 7 stops with the blue screen
UNSUPPORTED_PROCESSOR (0x5D) on current processors otherwise.
"""

TEMPLATES = [
    {"id": "windows11", "label": "Windows 11", "ostype": "Windows11_64", "memory": 8192, "cpus": 4,
     "disk_gb": 80, "firmware": "efi", "tpm": True, "secure_boot": True, "graphics": "vboxsvga", "vram": 256,
     "accelerate3d": True, "usb": "xhci", "nic": "82540EM", "family": "windows"},
    {"id": "windows10", "label": "Windows 10", "ostype": "Windows10_64", "memory": 4096, "cpus": 2,
     "disk_gb": 64, "firmware": "efi", "tpm": False, "secure_boot": False, "graphics": "vboxsvga", "vram": 256,
     "accelerate3d": True, "usb": "xhci", "nic": "82540EM", "family": "windows"},
    {"id": "windows7", "cpu_profile": "legacy", "label": "Windows 7", "ostype": "Windows7_64", "memory": 4096, "cpus": 2,
     "disk_gb": 50, "firmware": "bios", "tpm": False, "secure_boot": False, "graphics": "vboxsvga", "vram": 256,
     "accelerate3d": True, "usb": "ehci", "nic": "82540EM", "family": "windows"},
    {"id": "windowsxp", "cpu_profile": "legacy", "label": "Windows XP", "ostype": "WindowsXP", "memory": 1024, "cpus": 1,
     "disk_gb": 20, "firmware": "bios", "tpm": False, "secure_boot": False, "graphics": "vboxvga", "vram": 64,
     "accelerate3d": False, "usb": "ehci", "nic": "Am79C973", "storage": "ide", "audio": "ac97",
     "mouse": "ps2", "network": "none", "legacy": True, "family": "windows"},
    {"id": "windows2000", "cpu_profile": "legacy", "label": "Windows 2000", "ostype": "Windows2000", "memory": 512, "cpus": 1,
     "disk_gb": 10, "firmware": "bios", "tpm": False, "secure_boot": False, "graphics": "vboxvga", "vram": 32,
     "accelerate3d": False, "usb": "ehci", "nic": "Am79C973", "storage": "ide", "audio": "ac97",
     "mouse": "ps2", "ioapic": False, "network": "none", "legacy": True, "family": "windows"},
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
