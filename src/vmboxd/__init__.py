"""vmboxd - the VMBox toolstack daemon.

vmboxd plays the role that xenmgr plays in OpenXT: it owns the list of
virtual machines, enforces per-VM policy, handles USB assignment and the
switching of the display between VMs, and exposes all of that through a
small JSON API that the VMBox user interface and ``vmbox-ctl`` talk to.

Underneath, every VM is a regular VirtualBox machine driven through
``VBoxManage``.
"""

__version__ = "1.0.0"
