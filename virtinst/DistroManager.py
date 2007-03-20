#!/usr/bin/python -tt
#
# Convenience module for fetching/creating kernel/initrd files
# or bootable CD images.
#
# Copyright 2006-2007  Red Hat, Inc.
# Daniel P. Berrange <berrange@redhat.com>
#
# This software may be freely redistributed under the terms of the GNU
# general public license.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.

import logging
import os
import gzip
import re
import stat
import subprocess
import urlgrabber.grabber as grabber
import urlgrabber.progress as progress
import tempfile


# This is a generic base class for fetching/extracting files from
# a media source, such as CD ISO, NFS server, or HTTP/FTP server
class ImageFetcher:

    def __init__(self, location, scratchdir):
        self.location = location
        self.scratchdir = scratchdir

    def saveTemp(self, fileobj, prefix):
        (fd, fn) = tempfile.mkstemp(prefix="virtinst-" + prefix, dir=self.scratchdir)
        block_size = 16384
        try:
            while 1:
                buff = fileobj.read(block_size)
                if not buff:
                    break
                os.write(fd, buff)
        finally:
            os.close(fd)
        return fn

    def prepareLocation(self, progresscb):
        return True

    def cleanupLocation(self):
        pass

    def acquireFile(self, src, progresscb):
        raise "Must be subclassed"

# This is a fetcher capable of downloading from FTP / HTTP
class URIImageFetcher(ImageFetcher):

    def prepareLocation(self, progresscb):
        try:
            grabber.urlopen(self.location,
                            progress_obj = progresscb,
                            text = "Verifying install location...")
            return True
        except IOError, e:
            logging.debug("Validation failed for " + self.location + " " + str(e))
            return False

    def acquireFile(self, filename, progresscb):
        file = None
        try:
            base = os.path.basename(filename)
            logging.debug("Fetching URI " + self.location + "/" + filename)
            try:
                file = grabber.urlopen(self.location + "/" + filename,
                                       progress_obj = progresscb, \
                                       text = "Retrieving %s..." % base)
            except IOError, e:
                raise RuntimeError, "Invalid URL location given: " + str(e)
            tmpname = self.saveTemp(file, prefix=base + ".")
            logging.debug("Saved file to " + tmpname)
            return tmpname
        finally:
            if file:
                file.close()


# This is a fetcher capable of extracting files from a NFS server
# or loopback mounted file, or local CDROM device
class MountedImageFetcher(ImageFetcher):

    def prepareLocation(self, progresscb):
        cmd = None
        self.mntdir = tempfile.mkdtemp(prefix="virtinstmnt.", dir=self.scratchdir)
        logging.debug("Preparing mount at " + self.mntdir)
        if self.location.startswith("nfs:"):
            cmd = ["mount", "-o", "ro", self.location[4:], self.mntdir]
        else:
            if stat.S_ISBLK(os.stat(self.location)[stat.ST_MODE]):
                cmd = ["mount", "-o", "ro", self.location, self.mntdir]
            else:
                cmd = ["mount", "-o", "ro,loop", self.location, self.mntdir]
        ret = subprocess.call(cmd)
        if ret != 0:
            self.cleanupLocation()
            return False
        return True

    def cleanupLocation(self):
        logging.debug("Cleaning up mount at " + self.mntdir)
        cmd = ["umount", self.mntdir]
        ret = subprocess.call(cmd)
        try:
            os.rmdir(self.mntdir)
        except:
            pass

    def acquireFile(self, filename, progresscb):
        file = None
        try:
            logging.debug("Acquiring file from " + self.mntdir + "/" + filename)
            base = os.path.basename(filename)
            try:
                src = self.mntdir + "/" + filename
                if stat.S_ISDIR(os.stat(src)[stat.ST_MODE]):
                    pass
                else:
                    file = open(src, "r")
            except IOError, e:
                raise RuntimeError, "Invalid location given: " + str(e)
            except OSError, (errno, msg):
                raise RuntimeError, "Invalid location given: " + msg
            tmpname = self.saveTemp(file, prefix=base + ".")
            logging.debug("Saved file to " + tmpname)
            return tmpname
        finally:
            if file:
                file.close()

# An image store is a base class for retrieving either a bootable
# ISO image, or a kernel+initrd  pair for a particular OS distribution
class ImageStore:

    def __init__(self, uri, type=None, scratchdir=None):
        self.uri = uri
        self.type = type
        self.scratchdir = scratchdir

    def acquireBootDisk(self, fetcher, progresscb):
        raise "Not implemented"

    def acquireKernel(self, fetcher, progresscb):
        raise "Not implemented"

    def isValidStore(self, fetcher, progresscb):
        raise "Not implemented"


# Fedora image store is simple - we just fetch the required files
# straight out of the store.
class FedoraImageStore(ImageStore):

    def acquireKernel(self, fetcher, progresscb):
        if self.type is None:
            kernelpath = "images/pxeboot/vmlinuz"
            initrdpath = "images/pxeboot/initrd.img"
        else:
            kernelpath = "images/%s/vmlinuz" % (self.type)
            initrdpath = "images/%s/initrd.img" % (self.type)

        kernel = fetcher.acquireFile(kernelpath, progresscb)
        try:
            initrd = fetcher.acquireFile(initrdpath, progresscb)
            return (kernel, initrd, "method=" + fetcher.location)
        except:
            os.unlink(kernel)

    def acquireBootDisk(self, fetcher, progresscb):
        return fetcher.acquireFile("images/boot.iso", progresscb)

    def isValidStore(self, fetcher, progresscb):
        # No nice magic file that's consistent across all
        # versions. So RPM-GPG-KEY is best bet for now. Lets
        # hope other distros don't have the same named file
        ignore = None
        try:
            try:
                ignore = fetcher.acquireFile("RPM-GPG-KEY", progresscb)
                logging.debug("Detected a Fedora / RHEL distro")
                return True
            except RuntimeError, e:
                logging.debug("Doesn't look like a Fedora distro " + str(e))
                pass

            try:
                ignore = fetcher.acquireFile("RPM-GPG-KEY-redhat-release", progresscb)
                logging.debug("Detected a RHEL5.x distro")
                return True
            except RuntimeError, e:
                logging.debug("Doesn't look like a RHEL5.x distro " + str(e))
                pass
        finally:
            if ignore is not None:
                os.unlink(ignore)
        return False


# Suse  image store is harder - we fetch the kernel RPM and a helper
# RPM and then munge bits together to generate a initrd
class SuseImageStore(ImageStore):
    def acquireBootDisk(self, fetcher, progresscb):
        return fetcher.acquireFile("boot/boot.iso", progresscb)

    def acquireKernel(self, fetcher, progresscb):
        kernelrpm = None
        installinitrdrpm = None
        filelist = None
        try:
            # There is no predictable filename for kernel/install-initrd RPMs
            # so we have to grok the filelist and find them
            filelist = fetcher.acquireFile("ls-lR.gz", progresscb)
            (kernelrpmname, installinitrdrpmname) = self.extractRPMNames(filelist)

            # Now fetch the two RPMs we want
            kernelrpm = fetcher.acquireFile(kernelrpmname, progresscb)
            installinitrdrpm = fetcher.acquireFile(installinitrdrpmname, progresscb)

            # Process the RPMs to extract the kernel & generate an initrd
            return self.buildKernelInitrd(fetcher, kernelrpm, installinitrdrpm, progresscb)
        finally:
            if filelist is not None:
                os.unlink(filelist)
            if kernelrpm is not None:
                os.unlink(kernelrpm)
            if installinitrdrpm is not None:
                os.unlink(installinitrdrpm)

    # We need to parse the ls-lR.gz file, looking for the kernel &
    # install-initrd RPM entries - capturing the directory they are
    # in and the version'd filename.
    def extractRPMNames(self, filelist):
        filelistData = gzip.GzipFile(filelist, mode = "r")
        try:
            arch = os.uname()[4]
            arches = [arch]
            # On i686 arch, we also look under i585 and i386 dirs
            # in case the RPM is built for a lesser arch. We also
            # need the PAE variant (for Fedora dom0 at least)
            #
            # XXX shouldn't hard code that dom0 is PAE
            if arch == "i686":
                arches.append("i586")
                arches.append("i386")
                kernelname = "kernel-xenpae"

            installinitrdrpm = None
            kernelrpm = None
            dir = None
            while 1:
                data = filelistData.readline()
                if not data:
                    break
                if dir is None:
                    for arch in arches:
                        wantdir = "/suse/" + arch
                        if data == "." + wantdir + ":\n":
                            dir = wantdir
                            break
                else:
                    if data == "\n":
                        dir = None
                    else:
                        if data[:5] != "total":
                            filename = re.split("\s+", data)[8]

                            if filename[:14] == "install-initrd":
                                installinitrdrpm = dir + "/" + filename
                            elif filename[:len(kernelname)] == kernelname:
                                kernelrpm = dir + "/" + filename

            if kernelrpm is None:
                raise "Unable to determine kernel RPM path"
            if installinitrdrpm is None:
                raise "Unable to determine install-initrd RPM path"
            return (kernelrpm, installinitrdrpm)
        finally:
            filelistData.close()

    # We have a kernel RPM and a install-initrd RPM with a generic initrd in it
    # Now we have to munge the two together to build an initrd capable of
    # booting the installer.
    #
    # Yes, this is crazy ass stuff :-)
    def buildKernelInitrd(self, fetcher, kernelrpm, installinitrdrpm, progresscb):
        progresscb.start(text="Building initrd", size=11)
        progresscb.update(1)
        cpiodir = tempfile.mkdtemp(prefix="virtinstcpio.", dir=self.scratchdir)
        try:
            # Extract the kernel RPM contents
            os.mkdir(cpiodir + "/kernel")
            cmd = "cd " + cpiodir + "/kernel && (rpm2cpio " + kernelrpm + " | cpio --quiet -idm)"
            logging.debug("Running " + cmd)
            os.system(cmd)
            progresscb.update(2)

            # Determine the raw kernel version
            kernelinfo = None
            for f in os.listdir(cpiodir + "/kernel/boot"):
                if f.startswith("System.map-"):
                    kernelinfo = re.split("-", f)
            kernel_override = kernelinfo[1] + "-override-" + kernelinfo[3]
            kernel_version = kernelinfo[1] + "-" + kernelinfo[2] + "-" + kernelinfo[3]
            logging.debug("Got kernel version " + str(kernelinfo))

            # Build a list of all .ko files
            modpaths = {}
            for root, dirs, files in os.walk(cpiodir + "/kernel/lib/modules", topdown=False):
                for name in files:
                    if name.endswith(".ko"):
                        modpaths[name] = os.path.join(root, name)
            progresscb.update(3)

            # Extract the install-initrd RPM contents
            os.mkdir(cpiodir + "/installinitrd")
            cmd = "cd " + cpiodir + "/installinitrd && (rpm2cpio " + installinitrdrpm + " | cpio --quiet -idm)"
            logging.debug("Running " + cmd)
            os.system(cmd)
            progresscb.update(4)

            # Read in list of mods required for initrd
            modnames = []
            fn = open(cpiodir + "/installinitrd/usr/lib/install-initrd/" + kernelinfo[3] + "/module.list", "r")
            try:
                while 1:
                    line = fn.readline()
                    if not line:
                        break
                    line = line[:len(line)-1]
                    modnames.append(line)
            finally:
                fn.close()
            progresscb.update(5)

            # Uncompress the basic initrd
            cmd = "gunzip -c " + cpiodir + "/installinitrd/usr/lib/install-initrd/initrd-base.gz > " + cpiodir + "/initrd.img"
            logging.debug("Running " + cmd)
            os.system(cmd)
            progresscb.update(6)

            # Create temp tree to hold stuff we're adding to initrd
            moddir = cpiodir + "/initrd/lib/modules/" + kernel_override + "/initrd/"
            moddepdir = cpiodir + "/initrd/lib/modules/" + kernel_version
            os.makedirs(moddir)
            os.makedirs(moddepdir)
            os.symlink("../" + kernel_override, moddepdir + "/updates")
            os.symlink("lib/modules/" + kernel_override + "/initrd", cpiodir + "/initrd/modules")
            cmd = "cp " + cpiodir + "/installinitrd/usr/lib/install-initrd/" + kernelinfo[3] + "/module.config" + " " + moddir
            logging.debug("Running " + cmd)
            os.system(cmd)
            progresscb.update(7)

            # Copy modules we need into initrd staging dir
            for modname in modnames:
                if modpaths.has_key(modname):
                    src = modpaths[modname]
                    dst = moddir + "/" + modname
                    os.system("cp " + src + " " + dst)
            progresscb.update(8)

            # Run depmod across the staging area
            cmd = "depmod -a -b " + cpiodir + "/initrd -F " + cpiodir + "/kernel/boot/System.map-" + kernel_version + " " + kernel_version
            logging.debug("Running " + cmd)
            os.system(cmd)
            progresscb.update(9)

            # Add the extra modules to the basic initrd
            cmd = "cd " + cpiodir + "/initrd && ( find . | cpio --quiet -o -H newc -A -F " + cpiodir + "/initrd.img)"
            logging.debug("Running " + cmd)
            os.system(cmd)
            progresscb.update(10)

            # Compress the final initrd
            cmd = "gzip -f9N " + cpiodir + "/initrd.img"
            logging.debug("Running " + cmd)
            os.system(cmd)
            progresscb.end(11)

            # Save initrd & kernel to temp files for booting...
            initrdname = fetcher.saveTemp(open(cpiodir + "/initrd.img.gz", "r"), "initrd.img")
            logging.debug("Saved " + initrdname)
            try:
                kernelname = fetcher.saveTemp(open(cpiodir + "/kernel/boot/vmlinuz-" + kernel_version, "r"), "vmlinuz")
                logging.debug("Saved " + kernelname)
                return (kernelname, initrdname, "install=" + fetcher.location)
            except:
                os.unlink(initrdname)
        finally:
            #pass
            os.system("rm -rf " + cpiodir)


    def isValidStore(self, fetcher, progresscb):
        # Suse distros always have a 'directory.yast' file in the top
        # level of install tree, which we use as the magic check
        ignore = None
        try:
            try:
                ignore = fetcher.acquireFile("directory.yast", progresscb)
                logging.debug("Detected a Suse distro")
                return True
            except RuntimeError, e:
                logging.debug("Doesn't look like a Suse distro " + str(e))
                pass
        finally:
            if ignore is not None:
                os.unlink(ignore)
        return False


class DebianImageStore(ImageStore):
    def isValidStore(self, fetcher, progresscb):
        # Don't support any paravirt installs
        if self.type is not None:
            return False

        file = None
        try:
            try:
                file = fetcher.acquireFile("current/images/MANIFEST", progresscb)
            except RuntimeError, e:
                logging.debug("Doesn't look like a Debian distro " + str(e))
                return False
            f = open(file, "r")
            try:
                while 1:
                    buf = f.readline()
                    if not buf:
                        break
                    if re.match(".*debian.*", buf):
                        logging.debug("Detected a Debian distro")
                        return True
            finally:
                f.close()
        finally:
            if file is not None:
                os.unlink(file)
        return False

    def acquireBootDisk(self, fetcher, progresscb):
        # eg from http://ftp.egr.msu.edu/debian/dists/sarge/main/installer-i386/
        return fetcher.acquireFile("current/images/netboot/mini.iso", progresscb)


class UbuntuImageStore(ImageStore):
    def isValidStore(self, fetcher, progresscb):
        # Don't support any paravirt installs
        if self.type is not None:
            return False
        return False

class GentooImageStore(ImageStore):
    def isValidStore(self, fetcher, progresscb):
        # Don't support any paravirt installs
        if self.type is not None:
            return False
        return False

class MandrivaImageStore(ImageStore):
    def isValidStore(self, fetcher, progresscb):
        # Don't support any paravirt installs
        if self.type is not None:
            return False

        # Mandriva websites / media appear to have a VERSION
        # file in top level which we can use as our 'magic'
        # check for validity
        version = None
        try:
            try:
                version = fetcher.acquireFile("VERSION")
            except:
                return False
            f = open(version, "r")
            try:
                info = f.readline()
                if info.startswith("Mandriva"):
                    logging.debug("Detected a Mandriva distro")
                    return True
            finally:
                f.close()
        finally:
            if version is not None:
                os.unlink(version)

        return False

    def acquireBootDisk(self, fetcher, progresscb):
        #
        return fetcher.acquireFile("install/images/boot.iso", progresscb)

def _fetcherForURI(uri, scratchdir=None):
    if uri.startswith("http://") or uri.startswith("ftp://"):
        return URIImageFetcher(uri, scratchdir)
    else:
        return MountedImageFetcher(uri, scratchdir)

def _storeForDistro(fetcher, baseuri, type, progresscb, distro=None, scratchdir=None):
    stores = []
    if distro == "fedora" or distro is None:
        stores.append(FedoraImageStore(baseuri, type, scratchdir))
    if distro == "suse" or distro is None:
        stores.append(SuseImageStore(baseuri, type, scratchdir))
    if distro == "debian" or distro is None:
        stores.append(DebianImageStore(baseuri, type, scratchdir))
    if distro == "ubuntu" or distro is None:
        stores.append(UbuntuImageStore(baseuri, type, scratchdir))
    if distro == "gentoo" or distro is None:
        stores.append(GentooImageStore(baseuri, type, scratchdir))
    if distro == "mandriva" or distro is None:
        stores.append(MandrivaImageStore(baseuri, type, scratchdir))

    for store in stores:
        if store.isValidStore(fetcher, progresscb):
            return store

    raise RuntimeError, "Could not find an installable distribution the install location"


# Method to fetch a krenel & initrd pair for a particular distro / HV type
def acquireKernel(baseuri, progresscb, scratchdir="/var/tmp", type=None, distro=None):
    fetcher = _fetcherForURI(baseuri, scratchdir)
    if not fetcher.prepareLocation(progresscb):
        raise RuntimeError, "Invalid install location"

    try:
        store = _storeForDistro(fetcher=fetcher, baseuri=baseuri, type=type, \
                                progresscb=progresscb, distro=distro, scratchdir=scratchdir)
        return store.acquireKernel(fetcher, progresscb)
    finally:
        fetcher.cleanupLocation()

# Method to fetch a bootable ISO image for a particular distro / HV type
def acquireBootDisk(baseuri, progresscb, scratchdir="/var/tmp", type=None, distro=None):
    fetcher = _fetcherForURI(baseuri, scratchdir)
    if not fetcher.prepareLocation(progresscb):
        raise RuntimeError, "Invalid install location"

    try:
        store = _storeForDistro(fetcher=fetcher, baseuri=baseuri, type=type, \
                                progresscb=progresscb, distro=distro, scratchdir=scratchdir)
        return store.acquireBootDisk(fetcher, progresscb)
    finally:
        fetcher.cleanupLocation()

