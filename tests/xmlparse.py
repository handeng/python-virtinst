#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free  Software Foundation; either version 2 of the License, or
# (at your option)  any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
# MA 02110-1301 USA.

import unittest
import glob
import traceback

import virtinst

import tests

conn = tests.open_testdriver()

def sanitize_file_xml(xml):
    # s/"/'/g from generated XML, matches what libxml dumps out
    # This won't work all the time, but should be good enough for testing
    return xml.replace("'", "\"")

class XMLParseTest(unittest.TestCase):

    def _roundtrip_compare(self, filename):
        expectXML = sanitize_file_xml(file(filename).read())
        guest = virtinst.Guest(connection=conn, parsexml=expectXML)
        actualXML = guest.get_config_xml()
        tests.diff_compare(actualXML, expect_out=expectXML)

    def _alter_compare(self, actualXML, outfile):
        tests.test_create(conn, actualXML)
        tests.diff_compare(actualXML, outfile)

    def testRoundTrip(self):
        """
        Make sure parsing doesn't output different XML
        """
        exclude = ["misc-xml-escaping.xml"]
        failed = False
        error = ""
        for f in glob.glob("tests/xmlconfig-xml/*.xml"):
            if filter(f.endswith, exclude):
                continue

            try:
                self._roundtrip_compare(f)
            except Exception:
                failed = True
                error += "%s:\n%s\n" % (f, "".join(traceback.format_exc()))

        if failed:
            raise AssertionError("Roundtrip parse tests failed:\n%s" % error)

    def _set_and_check(self, obj, param, initval, newval="SENTINEL"):
        """
        Check expected initial value obj.param == initval, then
        set newval, and make sure it is returned properly
        """
        curval = getattr(obj, param)
        self.assertEquals(initval, curval)

        if newval == "SENTINEL":
            return
        setattr(obj, param, newval)
        curval = getattr(obj, param)
        self.assertEquals(newval, curval)

    def _make_checker(self, obj):
        def check(name, initval, newval="SENTINEL"):
            return self._set_and_check(obj, name, initval, newval)
        return check

    def testAlterGuest(self):
        """
        Test changing Guest() parameters after parsing
        """
        infile  = "tests/xmlparse-xml/change-guest-in.xml"
        outfile = "tests/xmlparse-xml/change-guest-out.xml"
        guest = virtinst.Guest(connection=conn,
                               parsexml=file(infile).read())

        check = self._make_checker(guest)

        check("name", "TestGuest", "change_name")
        check("description", None, "Hey desc changed")
        check("vcpus", 5, 28)
        check("cpuset", "1-3", "1-5,15")
        check("maxmemory", 400, 500)
        check("memory", 200, 1000)
        check("maxmemory", 1000, 2000)
        check("uuid", "12345678-1234-1234-1234-123456789012",
                      "11111111-2222-3333-4444-555555555555")

        check = self._make_checker(guest.clock)
        check("offset", "utc", "localtime")

        check = self._make_checker(guest.seclabel)
        check("type", "static", "static")
        check("model", "selinux", "apparmor")
        check("label", "foolabel", "barlabel")
        check("imagelabel", "imagelabel", "fooimage")

        check = self._make_checker(guest.installer)
        check("type", "kvm", "test")
        check("os_type", "hvm", "xen")
        check("arch", "i686", None)

        check = self._make_checker(guest.installer.bootconfig)
        check("bootorder", ["hd"], ["fd"])
        check("enable_bootmenu", None, False)
        check("kernel", None)
        check("initrd", None)
        check("kernel_args", None)

        check = self._make_checker(guest.features)
        check("acpi", True, False)
        check("apic", True, False)
        check("pae", False, True)
        self._alter_compare(guest.get_config_xml(), outfile)

    def testAlterBootMulti(self):
        infile  = "tests/xmlparse-xml/change-boot-multi-in.xml"
        outfile = "tests/xmlparse-xml/change-boot-multi-out.xml"
        guest = virtinst.Guest(connection=conn,
                               parsexml=file(infile).read())

        check = self._make_checker(guest.installer.bootconfig)
        check("bootorder", ['hd', 'fd', 'cdrom', 'network'], ["cdrom"])
        check("enable_bootmenu", False, True)
        check("kernel", None, "foo.img")
        check("initrd", None, "bar.img")
        check("kernel_args", None, "ks=foo.ks")

        self._alter_compare(guest.get_config_xml(), outfile)

    def testAlterBootKernel(self):
        infile  = "tests/xmlparse-xml/change-boot-kernel-in.xml"
        outfile = "tests/xmlparse-xml/change-boot-kernel-out.xml"
        guest = virtinst.Guest(connection=conn,
                               parsexml=file(infile).read())

        check = self._make_checker(guest.installer.bootconfig)
        check("bootorder", [], ["network", "hd", "fd"])
        check("enable_bootmenu", None)
        check("kernel", "/boot/vmlinuz", None)

        check("initrd", "/boot/initrd", None)
        check("kernel_args", "location", None)

        self._alter_compare(guest.get_config_xml(), outfile)

    def testAlterDisk(self):
        """
        Test changing VirtualDisk() parameters after parsing
        """
        infile  = "tests/xmlparse-xml/change-disk-in.xml"
        outfile = "tests/xmlparse-xml/change-disk-out.xml"
        guest = virtinst.Guest(connection=conn,
                               parsexml=file(infile).read())

        # XXX: Set size up front. VirtualDisk validation is kind of
        # convoluted. If trying to change a non-existing one and size wasn't
        # already specified, we will error out.
        disk1 = guest.disks[0]
        disk1.size = 1
        disk2 = guest.disks[2]
        disk2.size = 1
        disk3 = guest.disks[5]
        disk3.size = 1

        check = self._make_checker(disk1)
        check("path", "/tmp/test.img", "/dev/loop0")
        check("driver_name", None, "test")
        check("driver_type", None, "foobar")

        check = self._make_checker(disk2)
        check("path", "/dev/loop0", None)
        check("device", "cdrom", "floppy")
        check("read_only", True, False)
        check("target", None, "fde")
        check("bus", None, "fdc")

        check = self._make_checker(disk3)
        check("path", None, "/default-pool/default-vol")
        check("shareable", False, True)
        check("driver_cache", None, "writeback")

        self._alter_compare(guest.get_config_xml(), outfile)

    def testSingleDisk(self):
        xml = ("""<disk type="file" device="disk"><source file="/a.img"/>"""
               """<target dev="hda" bus="ide"/></disk>""")
        d = virtinst.VirtualDisk(parsexml=xml)
        self._set_and_check(d, "target", "hda", "hdb")
        self.assertEquals(xml.replace("hda", "hdb"), d.get_xml_config())

    def testAlterChars(self):
        infile  = "tests/xmlparse-xml/change-chars-in.xml"
        outfile = "tests/xmlparse-xml/change-chars-out.xml"
        guest = virtinst.Guest(connection=conn,
                               parsexml=file(infile).read())

        serial1     = guest.get_devices("serial")[0]
        serial2     = guest.get_devices("serial")[1]
        parallel1   = guest.get_devices("parallel")[0]
        parallel2   = guest.get_devices("parallel")[1]
        console1    = guest.get_devices("console")[0]
        console2    = guest.get_devices("console")[1]
        channel1    = guest.get_devices("channel")[0]
        channel2    = guest.get_devices("channel")[1]

        check = self._make_checker(serial1)
        check("char_type", "null")

        check = self._make_checker(serial2)
        check("char_type", "tcp")
        check("protocol", "telnet", "raw")
        check("source_mode", "bind", "connect")

        check = self._make_checker(parallel1)
        check("source_mode", "bind")
        check("source_path", "/tmp/foobar", None)
        check("char_type", "unix", "pty")

        check = self._make_checker(parallel2)
        check("char_type", "udp")
        check("bind_port", "1111", "1357")
        check("bind_host", "my.bind.host", "my.foo.host")
        check("source_mode", "connect")
        check("source_port", "2222", "7777")
        check("source_host", "my.source.host", "source.foo.host")

        check = self._make_checker(console1)
        check("char_type", "file")
        check("source_path", "/tmp/foo.img", None)
        check("source_path", None, "/root/foo")
        check("target_type", "virtio")

        check = self._make_checker(console2)
        check("char_type", "pty")
        check("target_type", None)

        check = self._make_checker(channel1)
        check("char_type", "pty")
        check("target_type", "virtio")
        check("target_name", "foo.bar.frob", "test.changed")

        check = self._make_checker(channel2)
        check("char_type", "unix")
        check("target_type", "guestfwd")
        check("target_address", "1.2.3.4", "5.6.7.8")
        check("target_port", "4567", "1199")

        self._alter_compare(guest.get_config_xml(), outfile)

    def testAlterControllers(self):
        infile  = "tests/xmlparse-xml/change-controllers-in.xml"
        outfile = "tests/xmlparse-xml/change-controllers-out.xml"
        guest = virtinst.Guest(connection=conn,
                               parsexml=file(infile).read())

        dev1 = guest.get_devices("controller")[0]
        dev2 = guest.get_devices("controller")[1]
        dev3 = guest.get_devices("controller")[2]

        check = self._make_checker(dev1)
        check("type", "ide")
        check("index", "3", "1")

        check = self._make_checker(dev2)
        check("type", "virtio-serial")
        check("index", "0", "7")
        check("ports", "32", "5")
        check("vectors", "17", None)

        check = self._make_checker(dev3)
        check("type", "scsi")
        check("index", "1", "2")

        self._alter_compare(guest.get_config_xml(), outfile)

    def testAlterNics(self):
        infile  = "tests/xmlparse-xml/change-nics-in.xml"
        outfile = "tests/xmlparse-xml/change-nics-out.xml"
        guest = virtinst.Guest(connection=conn,
                               parsexml=file(infile).read())

        dev1 = guest.get_devices("interface")[0]
        dev2 = guest.get_devices("interface")[1]
        dev3 = guest.get_devices("interface")[2]
        dev4 = guest.get_devices("interface")[3]

        check = self._make_checker(dev1)
        check("type", "user")
        check("model", None, "testmodel")
        check("bridge", None, "br0")
        check("network", None, "route")
        check("macaddr", "11:11:11:11:11:11", "AA:AA:AA:AA:AA:AA")
        self.assertEquals(dev1.get_source(), None)

        check = self._make_checker(dev2)
        self.assertEquals(dev2.get_source(), "default")
        check("type", "network", "bridge")
        check("network", "default", None)
        check("bridge", None, "newbr0")
        check("model", "e1000", "virtio")

        check = self._make_checker(dev3)
        check("type", "bridge")
        check("bridge", "foobr0", "newfoo0")
        check("network", None, "default")
        check("macaddr", "22:22:22:22:22:22")
        check("target_dev", None, "test1")
        self.assertEquals(dev3.get_source(), "newfoo0")

        check = self._make_checker(dev4)
        check("type", "ethernet")
        check("source_dev", "eth0", "eth1")
        check("target_dev", "nic02", "nic03")
        check("target_dev", "nic03", None)
        self.assertEquals(dev4.get_source(), "eth1")

        self._alter_compare(guest.get_config_xml(), outfile)

    def testAlterInputs(self):
        infile  = "tests/xmlparse-xml/change-inputs-in.xml"
        outfile = "tests/xmlparse-xml/change-inputs-out.xml"
        guest = virtinst.Guest(connection=conn,
                               parsexml=file(infile).read())

        dev1 = guest.get_devices("input")[0]
        dev2 = guest.get_devices("input")[1]

        check = self._make_checker(dev1)
        check("type", "mouse", "tablet")
        check("bus", "ps2", "usb")

        check = self._make_checker(dev2)
        check("type", "tablet", "mouse")
        check("bus", "usb", "xen")
        check("bus", "xen", "usb")

        self._alter_compare(guest.get_config_xml(), outfile)

    def testAlterGraphics(self):
        infile  = "tests/xmlparse-xml/change-graphics-in.xml"
        outfile = "tests/xmlparse-xml/change-graphics-out.xml"
        guest = virtinst.Guest(connection=conn,
                               parsexml=file(infile).read())

        dev1 = guest.get_devices("graphics")[0]
        dev2 = guest.get_devices("graphics")[1]
        dev3 = guest.get_devices("graphics")[2]
        dev4 = guest.get_devices("graphics")[3]

        check = self._make_checker(dev1)
        check("type", "vnc")
        check("passwd", "foobar", "newpass")
        check("port", 100, 6000)
        check("listen", "0.0.0.0", "1.2.3.4")

        check = self._make_checker(dev2)
        check("type", "sdl")

        check = self._make_checker(dev3)
        check("type", "rdp")

        check = self._make_checker(dev4)
        check("type", "vnc")
        check("port", -1)

        self._alter_compare(guest.get_config_xml(), outfile)

    def testAlterVideos(self):
        infile  = "tests/xmlparse-xml/change-videos-in.xml"
        outfile = "tests/xmlparse-xml/change-videos-out.xml"
        guest = virtinst.Guest(connection=conn,
                               parsexml=file(infile).read())

        dev1 = guest.get_devices("video")[0]
        dev2 = guest.get_devices("video")[1]
        dev3 = guest.get_devices("video")[2]

        check = self._make_checker(dev1)
        check("model_type", "vmvga", "vga")
        check("vram", None, "1000")
        check("heads", None, "1")

        check = self._make_checker(dev2)
        check("model_type", "cirrus", "vmvga")
        check("vram", "10240", None)
        check("heads", "3", "5")

        check = self._make_checker(dev3)
        check("model_type", "cirrus", "cirrus")

        self._alter_compare(guest.get_config_xml(), outfile)

    def testAlterHostdevs(self):
        infile  = "tests/xmlparse-xml/change-hostdevs-in.xml"
        outfile = "tests/xmlparse-xml/change-hostdevs-out.xml"
        guest = virtinst.Guest(connection=conn,
                               parsexml=file(infile).read())

        dev1 = guest.get_devices("hostdev")[0]
        dev2 = guest.get_devices("hostdev")[1]
        dev3 = guest.get_devices("hostdev")[2]

        check = self._make_checker(dev1)
        check("type", "usb")
        check("managed", True, False)
        check("mode", "subsystem", None)
        check("vendor", "0x4321", "0x1111")
        check("product", "0x1234", "0x2222")
        check("bus", None, "1")
        check("device", None, "2")

        check = self._make_checker(dev2)
        check("type", "usb")
        check("managed", False, True)
        check("mode", "capabilities", "subsystem")
        check("bus", "0x12", "0x56")
        check("device", "0x34", "0x78")

        check = self._make_checker(dev3)
        check("type", "pci")
        check("managed", True, True)
        check("mode", "subsystem", "capabilities")
        check("domain", "0x0", "0x2")
        check("bus", "0x11", "0x99")
        check("slot", "0x22", "0x88")
        check("function", "0x33", "0x77")

        self._alter_compare(guest.get_config_xml(), outfile)

    def testAlterWatchdogs(self):
        infile  = "tests/xmlparse-xml/change-watchdogs-in.xml"
        outfile = "tests/xmlparse-xml/change-watchdogs-out.xml"
        guest = virtinst.Guest(connection=conn,
                               parsexml=file(infile).read())

        dev1 = guest.get_devices("watchdog")[0]
        check = self._make_checker(dev1)
        check("model", "ib700", "i6300esb")
        check("action", "none", "poweroff")

        check = self._make_checker(dev1)
        self._alter_compare(guest.get_config_xml(), outfile)

    def testAlterSounds(self):
        infile  = "tests/xmlparse-xml/change-sounds-in.xml"
        outfile = "tests/xmlparse-xml/change-sounds-out.xml"
        guest = virtinst.Guest(connection=conn,
                               parsexml=file(infile).read())

        dev1 = guest.get_devices("sound")[0]
        dev2 = guest.get_devices("sound")[1]
        dev3 = guest.get_devices("sound")[2]

        check = self._make_checker(dev1)
        check("model", "sb16", "ac97")

        check = self._make_checker(dev2)
        check("model", "es1370", "es1370")

        check = self._make_checker(dev3)
        check("model", "ac97", "sb16")

        self._alter_compare(guest.get_config_xml(), outfile)

    def testConsoleCompat(self):
        infile  = "tests/xmlparse-xml/console-compat-in.xml"
        outfile = "tests/xmlparse-xml/console-compat-out.xml"
        guest = virtinst.Guest(connection=conn,
                               parsexml=file(infile).read())

        dev1 = guest.get_devices("console")[0]
        check = self._make_checker(dev1)
        check("source_path", "/dev/pts/4")

        self._alter_compare(guest.get_config_xml(), outfile)

if __name__ == "__main__":
    unittest.main()
