import os
import tempfile
import unittest

import context  # noqa: F401
from vmboxd.display import VirtualDisplay
from vmboxd.fakevbox import FakeVBoxManage
from vmboxd.toolstack import Toolstack, ToolstackError
from vmboxd.vbox import VBoxManage


class ToolstackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.iso_dir = os.path.join(self.tmp.name, "isos")
        os.makedirs(self.iso_dir)
        self.fake = FakeVBoxManage()
        self.vbox = VBoxManage(runner=self.fake)
        self.display = VirtualDisplay(running=self.vbox.running_vms)
        self.ts = Toolstack(self.vbox, self.display, self.iso_dir, cache_ttl=0)
        self.ts._focus_when_ready = lambda uuid, timeout=0: self.display.show_vm(uuid)

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, name="Büro", **spec):
        spec.setdefault("template", "ubuntu")
        return self.ts.create_vm(dict(name=name, **spec))

    # -- creation -------------------------------------------------------------
    def test_create_applies_template_policy_and_hardening(self):
        vm = self.create(template="windows11")
        self.assertEqual(vm["memory"], 8192)
        self.assertEqual(vm["firmware"], "efi")
        self.assertEqual(vm["slot"], 1)
        self.assertEqual(vm["type"], "user")
        self.assertEqual(vm["network"], "nat")
        self.assertEqual(vm["policy"]["clipboard"], "off")
        info = self.vbox.vm_info(vm["uuid"])
        self.assertEqual(info["vrde"], "off")
        self.assertEqual(info["natlocalhostreachable1"], "off")
        self.assertEqual(info["draganddrop"], "disabled")
        self.assertEqual(info["tpm_type"], "2.0")
        extradata = self.vbox.extradata(vm["uuid"])
        self.assertEqual(extradata["GUI/MenuBar/Enabled"], "false")
        self.assertTrue(vm["disk"].endswith("Büro.vdi"))

    def test_windows7_template_gets_usb2_e1000_and_3d(self):
        vm = self.create("Win7", template="windows7")
        info = self.vbox.vm_info(vm["uuid"])
        self.assertEqual(vm["firmware"], "bios")
        self.assertTrue(vm["accelerate3d"])
        self.assertEqual(info["vram"], "256")
        self.assertEqual(info["nictype1"], "82540EM")
        self.assertEqual((info["xhci"], info["ehci"], info["usb"]), ("off", "on", "on"))
        # USB 2.0 is kept when the policy is re-applied before a start
        self.ts.action(vm["uuid"], "start")
        self.assertEqual(self.vbox.vm_info(vm["uuid"])["ehci"], "on")

    def test_legacy_windows_templates_use_period_hardware(self):
        for template, ioapic in (("windowsxp", "on"), ("windows2000", "off")):
            vm = self.create(template, template=template)
            info = self.vbox.vm_info(vm["uuid"])
            self.assertEqual(vm["controller"], "IDE")
            self.assertTrue(vm["disk"].endswith(".vdi"))
            self.assertEqual(info["IDE-1-0"], "emptydrive")
            self.assertEqual(info["graphicscontroller"], "vboxvga")
            self.assertEqual(info["nictype1"], "Am79C973")
            self.assertEqual(info["audiocontroller"], "ac97")
            self.assertEqual(info["mouse"], "ps2")
            self.assertEqual(info["ioapic"], ioapic)
            self.assertEqual(vm["network"], "none")
            self.assertFalse(vm["accelerate3d"])
            with self.assertRaises(ToolstackError):
                self.ts.update_vm(vm["uuid"], {"accelerate3d": True})
            # ISO hot-swap and the CD policy work on the IDE controller too
            with open(os.path.join(self.iso_dir, "xp.iso"), "w") as fh:
                fh.write("x")
            self.ts.update_vm(vm["uuid"], {"iso": "xp.iso"})
            self.assertTrue(self.ts.get_vm(vm["uuid"])["iso"].endswith("xp.iso"))

    def test_old_windows_get_a_cpu_profile_matching_the_host_vendor(self):
        for vendor, profile in (("amd", "AMD Ryzen 7 1800X Eight-Core"), ("intel", "Intel Core i7-6700K")):
            self.ts.cpu_vendor = lambda v=vendor: v
            vm = self.create("Win7-" + vendor, template="windows7")
            info = self.vbox.vm_info(vm["uuid"])
            self.assertEqual((info["cpuprofile"], info["pae"]), (profile, "on"))
            self.assertEqual(vm["cpu_profile"], profile)
        modern = self.create("Win11", template="windows11")
        self.assertNotIn("cpuprofile", self.vbox.vm_info(modern["uuid"]))

    def test_existing_windows7_vm_gets_cpu_profile_on_next_start(self):
        self.ts.cpu_vendor = lambda: "amd"
        vm = self.create("Win7", template="windows7")
        # simulate a VM created by an older VMBox version without the profile
        self.vbox.set_extradata(vm["uuid"], "VMBox/CPUProfile", None)
        self.vbox.modify(vm["uuid"], "--cpu-profile", "host")
        self.ts.action(vm["uuid"], "start")
        self.assertEqual(self.vbox.vm_info(vm["uuid"])["cpuprofile"], "AMD Ryzen 7 1800X Eight-Core")

    def test_cpu_profile_and_spec_ctrl_can_be_chosen(self):
        self.ts.cpu_vendor = lambda: "amd"
        vm = self.create("Win7", template="windows7")
        self.assertIn("Intel Core i5-3570", self.ts.cpu_profiles())
        vm = self.ts.update_vm(vm["uuid"], {"cpu_profile": "Intel Core i5-3570", "spec_ctrl": True})
        info = self.vbox.vm_info(vm["uuid"])
        self.assertEqual((info["cpuprofile"], info["specctrl"]), ("Intel Core i5-3570", "on"))
        self.assertEqual((vm["cpu_profile"], vm["spec_ctrl"]), ("Intel Core i5-3570", True))
        with self.assertRaises(ToolstackError):
            self.ts.update_vm(vm["uuid"], {"cpu_profile": "Erfundene CPU"})
        # "host" is a deliberate choice: the legacy default must not overwrite it on start
        self.ts.update_vm(vm["uuid"], {"cpu_profile": "host"})
        self.ts.action(vm["uuid"], "start")
        self.assertEqual(self.vbox.vm_info(vm["uuid"])["cpuprofile"], "host")
        with self.assertRaises(ToolstackError):
            self.ts.update_vm(vm["uuid"], {"spec_ctrl": False})   # running

    def test_discard_saved_state(self):
        vm = self.create()
        self.ts.action(vm["uuid"], "start")
        self.ts.action(vm["uuid"], "save")
        self.ts.action(vm["uuid"], "discard")
        self.assertEqual(self.ts.get_vm(vm["uuid"])["state"], "stopped")
        with self.assertRaises(ToolstackError):
            self.ts.action(vm["uuid"], "discard")

    def test_vesa_modes_match_the_screen_for_guests_without_drivers(self):
        self.display.screen_size = lambda: (1366, 768)
        vm = self.create("Win7", template="windows7")
        extra = self.vbox.extradata(vm["uuid"])
        # exact window below the 37 px bar, full screen (width rounded to 8), common sizes
        self.assertEqual(extra["CustomVideoMode1"], "1360x731x32")
        self.assertEqual(extra["CustomVideoMode2"], "1360x768x32")
        self.assertIn("1920x1080x32", extra.values())
        # a different monitor is picked up on the next cold start
        self.display.screen_size = lambda: (2560, 1440)
        self.ts.action(vm["uuid"], "start")
        self.assertEqual(self.vbox.extradata(vm["uuid"])["CustomVideoMode1"], "2560x1403x32")

    def test_guest_with_additions_gets_window_size_as_resolution(self):
        self.display.screen_size = lambda: (1920, 1200)
        vm = self.create("Win7", template="windows7")
        self.ts.action(vm["uuid"], "start")
        info = self.fake.vms[vm["uuid"]]["info"]
        self.ts.apply_resolution_hints()
        self.assertNotIn("VideoModeHint", info)          # Guest Additions not running yet
        info["GuestAdditionsRunLevel"] = "3"
        self.ts.apply_resolution_hints()
        self.assertEqual(info["VideoModeHint"], "1920x1163x32")
        calls = len(self.fake.calls)
        self.ts.apply_resolution_hints()                  # same size: no new hint
        self.assertFalse(any(c[:3] == ["controlvm", vm["uuid"], "setvideomodehint"] for c in self.fake.calls[calls:]))
        self.display.screen_size = lambda: (2560, 1440)   # other monitor
        self.ts.apply_resolution_hints()
        self.assertEqual(info["VideoModeHint"], "2560x1403x32")

    def test_resolution_button_and_diagnostics(self):
        vm = self.create("Win7", template="windows7")
        with self.assertRaises(ToolstackError):
            self.ts.request_resolution(vm["uuid"])            # not running
        self.ts.action(vm["uuid"], "start")
        info = self.fake.vms[vm["uuid"]]["info"]
        info["VideoMode"] = '1280,1024,32"@0,0 1'
        with self.assertRaises(ToolstackError) as ctx:
            self.ts.request_resolution(vm["uuid"])            # no Guest Additions
        self.assertIn("Gasterweiterungen", str(ctx.exception))
        info["GuestAdditionsVersion"] = "7.2.4 r170995"
        info["GuestAdditionsRunLevel"] = "3"
        diag = self.ts.request_resolution(vm["uuid"])
        self.assertEqual(diag["guest"], [1280, 1024])
        self.assertEqual(diag["window"], [1920, 1043])         # actual window wins over screen maths
        self.assertEqual(info["VideoModeHint"], "1920x1043x32")
        self.assertEqual(self.ts.display_info(vm["uuid"])["guest"], [1920, 1043])

    def test_3d_can_be_toggled_while_stopped(self):
        vm = self.create(template="linux")
        self.assertTrue(vm["accelerate3d"])
        vm = self.ts.update_vm(vm["uuid"], {"accelerate3d": False})
        self.assertFalse(vm["accelerate3d"])
        self.ts.action(vm["uuid"], "start")
        with self.assertRaises(ToolstackError):
            self.ts.update_vm(vm["uuid"], {"accelerate3d": True})

    def test_slots_are_allocated_and_unique(self):
        a = self.create("A")
        b = self.create("B")
        self.assertEqual((a["slot"], b["slot"]), (1, 2))
        with self.assertRaises(ToolstackError):
            self.ts.update_vm(b["uuid"], {"slot": 1})

    def test_duplicate_and_invalid_names(self):
        self.create("A")
        with self.assertRaises(ToolstackError):
            self.create("a")
        with self.assertRaises(ToolstackError):
            self.create("../etc")

    def test_service_vm_is_hidden_and_autostarts(self):
        vm = self.create("Netz", template="service")
        self.assertEqual(vm["type"], "service")
        self.assertTrue(vm["hidden"])
        self.assertTrue(vm["start_on_boot"])

    def test_iso_must_live_in_iso_dir(self):
        with open(os.path.join(self.iso_dir, "debian.iso"), "w") as fh:
            fh.write("x")
        vm = self.create(iso="debian.iso")
        self.assertTrue(vm["iso"].endswith("debian.iso"))
        with self.assertRaises(ToolstackError):
            self.create("B", iso="/etc/passwd")
        with self.assertRaises(ToolstackError):
            self.create("C", iso="../../etc/passwd")

    def test_failed_creation_is_cleaned_up(self):
        original = self.fake.cmd_storagectl

        def broken(rest):
            raise __import__("vmboxd.fakevbox", fromlist=["FakeError"]).FakeError("boom")
        self.fake.cmd_storagectl = broken
        with self.assertRaises(Exception):
            self.create("Broken")
        self.fake.cmd_storagectl = original
        self.assertEqual(self.ts.list_vms(), [])

    # -- lifecycle --------------------------------------------------------------
    def test_lifecycle_and_switching(self):
        vm = self.create()
        self.ts.action(vm["uuid"], "start")
        self.assertEqual(self.ts.get_vm(vm["uuid"])["state"], "running")
        self.assertEqual(self.ts.focus(), vm["uuid"])
        self.ts.switch_ui()
        self.assertIsNone(self.ts.focus())
        self.ts.switch_to("1")
        self.assertEqual(self.ts.focus(), vm["uuid"])
        self.ts.action(vm["uuid"], "pause")
        self.assertEqual(self.ts.get_vm(vm["uuid"])["state"], "paused")
        self.ts.action(vm["uuid"], "resume")
        self.ts.action(vm["uuid"], "poweroff")
        self.assertEqual(self.ts.get_vm(vm["uuid"])["state"], "stopped")
        with self.assertRaises(ToolstackError):
            self.ts.action(vm["uuid"], "shutdown")

    def test_start_refused_with_clear_message_when_kvm_is_loaded(self):
        vm = self.create()
        self.ts.kvm_loaded = lambda: True
        with self.assertRaises(ToolstackError) as ctx:
            self.ts.action(vm["uuid"], "start")
        self.assertIn("KVM", str(ctx.exception))
        self.assertEqual(self.ts.get_vm(vm["uuid"])["state"], "stopped")

    def _frontend_setup(self):
        launched = []
        self.display.launch_frontend = launched.append
        self.display.has_window = lambda uuid: False
        clock = [1000.0]
        import vmboxd.toolstack as tsmod
        self._orig_monotonic = tsmod.time.monotonic
        tsmod.time.monotonic = lambda: clock[0]
        self.addCleanup(lambda: setattr(tsmod.time, "monotonic", self._orig_monotonic))
        return launched, clock

    def test_window_reattached_only_after_it_stays_missing(self):
        vm = self.create()
        self.ts.action(vm["uuid"], "start")
        launched, clock = self._frontend_setup()
        clock[0] += 25                       # start grace is over
        self.ts.ensure_frontends()
        self.assertEqual(launched, [])       # first miss: wait
        clock[0] += 11
        self.ts.ensure_frontends()
        self.assertEqual(launched, [vm["uuid"]])

    def test_no_window_for_a_vm_that_just_stopped(self):
        vm = self.create()
        self.ts.action(vm["uuid"], "start")
        launched, clock = self._frontend_setup()
        clock[0] += 25
        self.ts.ensure_frontends()
        # guest shuts itself down; our cached state still says "running"
        self.fake.vms[vm["uuid"]]["info"]["VMState"] = "poweroff"
        self.ts.cache_ttl = 3600
        clock[0] += 11
        self.ts.ensure_frontends()
        self.assertEqual(launched, [])       # would otherwise power the VM on again

    def test_guest_additions_can_be_inserted_into_a_running_vm(self):
        ga = os.path.join(self.tmp.name, "VBoxGuestAdditions.iso")
        open(ga, "w").close()
        self.ts.guest_additions_paths = (ga,)
        vm = self.create(template="windows7")
        self.ts.action(vm["uuid"], "start")
        vm = self.ts.insert_guest_additions(vm["uuid"])
        self.assertEqual(vm["iso"], ga)
        self.assertTrue(vm["guest_additions_inserted"])
        # and ejected again via the normal ISO field
        vm = self.ts.update_vm(vm["uuid"], {"iso": ""})
        self.assertFalse(vm["guest_additions_inserted"])

    def test_guest_additions_respect_cd_policy_and_availability(self):
        vm = self.create(policy={"cd": False})
        with self.assertRaises(ToolstackError) as ctx:
            self.ts.insert_guest_additions(vm["uuid"])
        self.assertEqual(ctx.exception.status, 403)
        other = self.create("B")
        self.ts.guest_additions_paths = (os.path.join(self.tmp.name, "missing.iso"),)
        with self.assertRaises(ToolstackError) as ctx:
            self.ts.insert_guest_additions(other["uuid"])
        self.assertEqual(ctx.exception.status, 404)

    def test_switch_cycle(self):
        a, b = self.create("A"), self.create("B")
        self.ts.action(a["uuid"], "start")
        self.ts.action(b["uuid"], "start")
        self.ts.switch_ui()
        self.ts.switch_cycle(1)
        self.assertEqual(self.ts.focus(), a["uuid"])
        self.ts.switch_cycle(1)
        self.assertEqual(self.ts.focus(), b["uuid"])
        self.ts.switch_cycle(1)
        self.assertIsNone(self.ts.focus())
        self.ts.switch_cycle(-1)
        self.assertEqual(self.ts.focus(), b["uuid"])

    def test_locked_screen_blocks_switching(self):
        vm = self.create()
        self.ts.action(vm["uuid"], "start")
        self.ts.is_locked = lambda: True
        with self.assertRaises(ToolstackError) as ctx:
            self.ts.switch_to(vm["uuid"])
        self.assertEqual(ctx.exception.status, 423)

    def test_service_vm_cannot_be_shown(self):
        vm = self.create("Netz", template="service")
        self.ts.action(vm["uuid"], "start")
        self.assertIn(["startvm", vm["uuid"], "--type", "headless"], self.fake.calls)
        with self.assertRaises(ToolstackError):
            self.ts.switch_to(vm["uuid"])

    def test_hardware_changes_require_stopped_vm(self):
        vm = self.create()
        self.ts.update_vm(vm["uuid"], {"memory": 2048, "color": "#43a047"})
        self.assertEqual(self.ts.get_vm(vm["uuid"])["memory"], 2048)
        self.ts.action(vm["uuid"], "start")
        with self.assertRaises(ToolstackError):
            self.ts.update_vm(vm["uuid"], {"memory": 4096})
        # clipboard and network can change at runtime
        self.ts.update_vm(vm["uuid"], {"policy": {"clipboard": "both"}, "network": "intnet:lab"})
        updated = self.ts.get_vm(vm["uuid"])
        self.assertEqual(updated["policy"]["clipboard"], "both")
        self.assertIn(["controlvm", vm["uuid"], "nic1", "intnet", "lab"], self.fake.calls)

    def test_enforce_policy_on_start(self):
        vm = self.create()
        # somebody re-enables VRDE behind our back
        self.vbox.modify(vm["uuid"], "--vrde", "on")
        self.ts.action(vm["uuid"], "start")
        self.assertEqual(self.vbox.vm_info(vm["uuid"])["vrde"], "off")

    def test_delete(self):
        vm = self.create()
        self.ts.action(vm["uuid"], "start")
        with self.assertRaises(ToolstackError):
            self.ts.delete_vm(vm["uuid"])
        self.ts.action(vm["uuid"], "poweroff")
        self.ts.delete_vm(vm["uuid"])
        self.assertEqual(self.ts.list_vms(), [])

    def test_save_all_and_resume_on_boot(self):
        vm = self.create()
        self.ts.action(vm["uuid"], "start")
        self.assertEqual(self.ts.save_all(), ["Büro"])
        self.assertTrue(self.ts.get_vm(vm["uuid"])["resume_on_boot"])
        self.ts.autostart()
        state = self.ts.get_vm(vm["uuid"])
        self.assertEqual(state["state"], "running")
        self.assertFalse(state["resume_on_boot"])

    # -- snapshots --------------------------------------------------------------
    def test_snapshots(self):
        vm = self.create()
        self.ts.take_snapshot(vm["uuid"], "Basis")
        self.ts.take_snapshot(vm["uuid"], "Update")
        snaps = self.ts.snapshots(vm["uuid"])
        self.assertEqual([s["name"] for s in snaps], ["Basis", "Update"])
        self.assertTrue(snaps[1]["current"])
        self.ts.restore_snapshot(vm["uuid"], snaps[0]["uuid"])
        self.assertTrue(self.ts.snapshots(vm["uuid"])[0]["current"])
        self.ts.delete_snapshot(vm["uuid"], snaps[1]["uuid"])
        self.assertEqual(len(self.ts.snapshots(vm["uuid"])), 1)

    # -- USB --------------------------------------------------------------------
    def test_usb_assignment_respects_policy(self):
        a = self.create("A")
        b = self.create("B", policy={"usb": False})
        self.ts.action(a["uuid"], "start")
        self.ts.action(b["uuid"], "start")
        dev = self.ts.usb_devices()[0]
        with self.assertRaises(ToolstackError):
            self.ts.usb_assign(dev["uuid"], b["uuid"])
        self.ts.usb_assign(dev["uuid"], a["uuid"])
        self.assertEqual(self.ts.usb_devices()[0]["vm"], a["uuid"])
        self.ts.usb_release(dev["uuid"])
        self.assertIsNone(self.ts.usb_devices()[0]["vm"])

    def test_sticky_usb_is_auto_assigned(self):
        vm = self.create()
        dev = self.ts.usb_devices()[1]
        self.ts.usb_set_sticky(dev["uuid"], vm["uuid"], True)
        self.assertEqual(self.ts.get_vm(vm["uuid"])["sticky_usb"], [dev["id"]])
        self.ts.action(vm["uuid"], "start")
        self.ts.usb_autoassign()
        self.assertEqual(self.ts.usb_devices()[1]["vm"], vm["uuid"])

    def test_networks(self):
        ids = [n["id"] for n in self.ts.networks()]
        self.assertIn("nat", ids)
        self.assertIn("bridged:enp3s0", ids)
        self.assertIn("intnet:vmbox-lan", ids)
        with self.assertRaises(ToolstackError):
            self.create(network="bridged:")


if __name__ == "__main__":
    unittest.main()
